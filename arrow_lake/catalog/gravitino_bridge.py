"""Bidirectional sync between DuckDB catalog_tables and Gravitino.

Registers datasets as Gravitino Tables (lance-catalog) and Filesets
(minio-fileset catalog) for metadata tracking. Tables carry column
schema and appear in the Gravitino Tables UI.

Architecture (post-P2A):
  * **Table** operations (lance-catalog create/drop) go through the
    hand-rolled ``_request`` urllib REST (P1 made it reliable: explicit
    exception classes + retry). Migrating these to the SDK requires a
    pyarrow→SDK ``Types`` mapping that is out of scope for P2A.
  * **Fileset + Schema** operations go through the official
    ``apache-gravitino`` SDK (``GravitinoClient``) — same default Simple
    auth the rest of the codebase already uses (e.g. ``gravitino_models``).
    This removes the fragile ``_fileset_exists`` cache and the hand-rolled
    fileset REST. SDK exceptions are translated to the Bridge's public
    ``GravitinoRequestError`` / ``GravitinoTransientError`` contract.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import structlog

from arrow_lake.catalog.gravitino_auth import create_auth_provider
from arrow_lake.config.gravitino import GravitinoConfig
from arrow_lake.workflow.retry import retry_with_backoff

logger = structlog.get_logger(__name__)


class GravitinoRequestError(Exception):
    """A Gravitino REST request failed with a non-transient error.

    Covers 4xx client errors (400/401/403/...). These are not retried —
    the request itself is malformed or unauthorized, retrying cannot help.
    Callers should surface the failure rather than silently treat it as
    "already exists" / "not found".
    """

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class GravitinoTransientError(GravitinoRequestError):
    """A transient Gravitino failure that is safe to retry.

    Covers 5xx server errors, network errors (URLError/OSError), and
    timeouts. Retried via ``retry_with_backoff`` and re-raised when
    attempts are exhausted.
    """


class _Idempotent(Exception):  # noqa: N818 - control-flow sentinel, not a real error
    """Internal sentinel: an SDK call raised an AlreadyExists/NotFound.

    Raised by ``_call_sdk`` so public methods can translate idempotent
    outcomes (create-if-exists / drop-if-absent / load-if-absent) into the
    Bridge's no-op semantics without a silent return.
    """


_PRIMITIVE_MAP: dict[str, str] = {
    "int8": "byte",
    "int16": "short",
    "int32": "integer",
    "int64": "long",
    "uint8": "byte",
    "uint16": "short",
    "uint32": "integer",
    "uint64": "long",
    "float": "float",
    "float16": "float",
    "float32": "float",
    "double": "double",
    "bool": "boolean",
    "string": "string",
    "large_string": "string",
    "utf8": "string",
    "binary": "binary",
    "large_binary": "binary",
    "date32": "date",
    "date32[day]": "date",
    "date64": "date",
    "date64[ms]": "date",
    "timestamp[s]": "timestamp",
    "timestamp[ms]": "timestamp",
    "timestamp[us]": "timestamp",
    "timestamp[ns]": "timestamp",
}


_DECIMAL_RE = re.compile(r"^decimal(?:128|256)?\((\d+),\s*(\d+)\)$")


def _arrow_type_to_gravitino(arrow_type: Any) -> str:
    """Convert a PyArrow type to a Gravitino type string.

    Gravitino's unified type system supports ``decimal(p,s)`` (p∈[1,38]),
    ``timestamp``/``timestamp_tz``, and the primitives in ``_PRIMITIVE_MAP``.
    Compound types (list/struct/map) require a JSON object form in the REST
    API (``{"type":"struct","fields":[...]}``) that this string mapper cannot
    express — they fall back to ``string`` with a warning so create-table
    still succeeds, trading fidelity on those columns for reliability.
    """
    type_str = str(arrow_type)

    # decimal128/256(p, s) → decimal(p, s); Gravitino caps precision at 38.
    if type_str.startswith("decimal"):
        m = _DECIMAL_RE.match(type_str)
        if m:
            precision = min(int(m.group(1)), 38)
            scale = min(int(m.group(2)), precision)
            return f"decimal({precision},{scale})"
        return "decimal(38,0)"

    # timestamp[us, tz=...] → timestamp_tz; plain timestamp[us] is in the map.
    if type_str.startswith("timestamp") and "tz=" in type_str:
        return "timestamp_tz"

    if type_str in _PRIMITIVE_MAP:
        return _PRIMITIVE_MAP[type_str]

    # Compound types + genuinely unknown types: warn, then safe fallback.
    logger.warning("gravitino_type_unmapped", arrow_type=type_str, fallback="string")
    return "string"


_FILESET_CATALOG = "minio-fileset"
_LANCE_CATALOG = "lance-catalog"
_MODEL_CATALOG = "ml-models"
_DEFAULT_SCHEMA = "arrow_lake"


@dataclass(frozen=True)
class SyncResult:
    """Result of a sync operation."""

    synced: int
    errors: int
    details: tuple[str, ...] = ()


class GravitinoBridge:
    """Bidirectional sync between local DuckDB catalog and Gravitino.

    Fileset/Schema operations use the official SDK; Table operations use
    the hand-rolled ``_request`` REST (see module docstring). Thread-safe:
    all REST/SDK interactions are serialized via ``_lock``.
    """

    def __init__(self, config: GravitinoConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._base = config.uri.rstrip("/")
        self._metalake = config.metalake
        self._auth_provider = create_auth_provider(config)
        self._headers = {
            "Accept": "application/vnd.gravitino.v1+json",
            "Content-Type": "application/json",
        }
        self._schema_ready = False
        # SDK GravitinoClient (lazy; default Simple auth, same as
        # gravitino_models). None when the SDK is unavailable.
        self._client: Any = None
        self._client_init_attempted = False

    @property
    def enabled(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # SDK client (fileset + schema operations)
    # ------------------------------------------------------------------

    def _ensure_client(self) -> Any:
        """Lazily build and cache a SDK ``GravitinoClient`` (Simple auth).

        Returns the client, or ``None`` if the SDK is missing or init fails
        (fileset/schema ops then no-op; tables still work via ``_request``).
        """
        if self._client is not None:
            return self._client
        if self._client_init_attempted:
            return None
        self._client_init_attempted = True
        try:
            from gravitino.client.gravitino_client import GravitinoClient

            self._client = GravitinoClient(
                uri=self._config.uri, metalake_name=self._metalake
            )
            logger.info(
                "gravitino_sdk_client_initialized",
                uri=self._config.uri,
                metalake=self._metalake,
            )
        except Exception as exc:
            logger.warning("gravitino_sdk_client_init_failed", error=str(exc))
            self._client = None
        return self._client

    @retry_with_backoff(
        max_attempts=3,
        min_backoff=0.2,
        max_backoff=5.0,
        retryable_exceptions=(GravitinoTransientError,),
    )
    def _call_sdk(self, fn: Any) -> Any:
        """Run an SDK call, translating exceptions.

          * ``AlreadyExistsException`` / ``NotFoundException`` → raise
            :class:`_Idempotent` (caller treats as a no-op).
          * ``InternalError`` (5xx) / network / timeout → raise
            :class:`GravitinoTransientError` (retried by the decorator).
          * Other ``RESTException`` (4xx) → raise
            :class:`GravitinoRequestError` (not retried).
        """
        from gravitino.exceptions.base import (
            AlreadyExistsException,
            InternalError,
            NotFoundException,
            RESTException,
        )

        try:
            return fn()
        except (AlreadyExistsException, NotFoundException) as exc:
            raise _Idempotent() from exc
        except InternalError as exc:
            raise GravitinoTransientError(f"gravitino sdk transient: {exc}") from exc
        except RESTException as exc:
            raise GravitinoRequestError(f"gravitino sdk: {exc}") from exc
        except (ConnectionError, OSError) as exc:
            raise GravitinoTransientError(f"gravitino sdk network: {exc}") from exc

    # ------------------------------------------------------------------
    # Hand-rolled REST (table operations only)
    # ------------------------------------------------------------------

    @retry_with_backoff(
        max_attempts=3,
        min_backoff=0.2,
        max_backoff=5.0,
        retryable_exceptions=(GravitinoTransientError,),
    )
    def _request(
        self, method: str, path: str, body: dict | None = None
    ) -> dict[str, Any] | None:
        """Send an authenticated request to the Gravitino REST API.

        Return semantics (idempotent-aware, no silent failures):
          * 2xx → parsed JSON body (``{"code": 0}`` for empty responses).
          * 409 Conflict / 404 Not Found → ``None``. These are idempotent
            outcomes ("already exists" / "not present"); callers treat them
            as no-ops, not failures.
          * Other 4xx → raise :class:`GravitinoRequestError` (not retried —
            the request is malformed/unauthorized).
          * 5xx / network / timeout → raise :class:`GravitinoTransientError`
            (retried with exponential backoff; re-raised when exhausted).
        """
        url = f"{self._base}{path}"
        data = json.dumps(body).encode() if body else None
        req = Request(url, data=data, headers=self._headers, method=method)
        self._auth_provider.authenticate(req)
        try:
            with urlopen(req, timeout=10) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {"code": 0}
        except HTTPError as e:
            if e.code in (409, 404):
                return None
            if 500 <= e.code < 600:
                raise GravitinoTransientError(
                    f"gravitino {method} {path} -> HTTP {e.code}", status=e.code
                ) from e
            raise GravitinoRequestError(
                f"gravitino {method} {path} -> HTTP {e.code}", status=e.code
            ) from e
        except (URLError, OSError) as exc:
            raise GravitinoTransientError(
                f"gravitino {method} {path} network error: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Schema (SDK)
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Idempotently ensure the default schema exists in both catalogs."""
        if self._schema_ready:
            return
        client = self._ensure_client()
        if client is None:
            # SDK unavailable — assume init-gravitino.sh already created it.
            self._schema_ready = True
            return
        for catalog_name in (_LANCE_CATALOG, _FILESET_CATALOG):
            try:
                catalog = client.load_catalog(catalog_name)
                # Skip create if the schema already exists. Gravitino's
                # createSchema verifies the S3 location *before* reporting
                # SchemaAlreadyExists, so re-ensuring on a fileset catalog
                # whose s3a creds are misconfigured surfaces a spurious 403.
                # schema_exists reads Gravitino's own metadata (no S3 call).
                if self._call_sdk(
                    lambda c=catalog: c.as_schemas().schema_exists(_DEFAULT_SCHEMA)
                ):
                    continue
                self._call_sdk(
                    lambda c=catalog: c.as_schemas().create_schema(
                        schema_name=_DEFAULT_SCHEMA,
                        comment="Arrow Lake datasets",
                        properties=None,
                    )
                )
            except _Idempotent:
                pass  # schema already exists
            except GravitinoRequestError as exc:
                logger.warning(
                    "gravitino_schema_ensure_failed",
                    catalog=catalog_name,
                    error=str(exc),
                )
        self._schema_ready = True

    # ------------------------------------------------------------------
    # Dataset lifecycle
    # ------------------------------------------------------------------

    def register_dataset(
        self, name: str, schema: Any = None, location: str = ""
    ) -> None:
        """Register a dataset in Gravitino as a Table with schema + a Fileset."""
        with self._lock:
            self._ensure_schema()
            if not location:
                location = f"s3a://arrow-lake/{name}.lance"

            # 1. Table in lance-catalog (hand-rolled REST, with columns).
            self._register_table(name, schema, location)

            # 2. Fileset in minio-fileset (SDK). Idempotent via SDK exception.
            client = self._ensure_client()
            if client is None:
                return
            s3_location = location.replace("s3a://", "s3://")
            try:
                from gravitino import NameIdentifier
                from gravitino.api.file.fileset import Fileset

                catalog = client.load_catalog(_FILESET_CATALOG)
                ident = NameIdentifier.of(_DEFAULT_SCHEMA, name)
                self._call_sdk(
                    lambda: catalog.as_fileset_catalog().create_multiple_location_fileset(
                        ident,
                        f"Arrow Lake dataset: {name}",
                        Fileset.Type.MANAGED,
                        {"default": s3_location},
                        {},
                    )
                )
                logger.info("gravitino_fileset_registered", name=name)
            except _Idempotent:
                logger.info("gravitino_fileset_exists", name=name)
            except GravitinoRequestError as exc:
                logger.warning(
                    "gravitino_fileset_register_failed", name=name, error=str(exc)
                )

    def _register_table(
        self, name: str, schema: Any, location: str
    ) -> None:
        """Register dataset as a Gravitino Table in lance-catalog."""
        columns = self._build_gravitino_columns(schema)
        s3_location = location.replace("s3a://", "s3://")
        body: dict[str, Any] = {
            "name": name,
            "comment": f"Arrow Lake table: {name}",
            "columns": columns,
            "properties": {
                "format": "lance",
                "location": s3_location,
            },
        }
        table_path = (
            f"/api/metalakes/{self._metalake}"
            f"/catalogs/{_LANCE_CATALOG}"
            f"/schemas/{_DEFAULT_SCHEMA}/tables"
        )
        result = self._request("POST", table_path, body)
        if result is not None:
            logger.info(
                "gravitino_table_registered",
                name=name,
                columns=len(columns),
            )
        else:
            logger.info("gravitino_table_exists", name=name)

    def _build_gravitino_columns(self, schema: Any) -> list[dict[str, Any]]:
        """Convert PyArrow schema to Gravitino Table column definitions."""
        if schema is None:
            return [{"name": "data", "type": "string", "nullable": True}]
        try:
            import pyarrow as pa

            if isinstance(schema, pa.Schema):
                cols = []
                for field in schema:
                    cols.append({
                        "name": field.name,
                        "type": _arrow_type_to_gravitino(field.type),
                        "nullable": field.nullable,
                    })
                return cols if cols else [{"name": "data", "type": "string", "nullable": True}]
        except Exception:
            pass
        return [{"name": "data", "type": "string", "nullable": True}]

    def deregister_dataset(self, name: str) -> None:
        """Remove a dataset from Gravitino (table + fileset)."""
        with self._lock:
            # Table (hand-rolled REST).
            table_path = (
                f"/api/metalakes/{self._metalake}"
                f"/catalogs/{_LANCE_CATALOG}"
                f"/schemas/{_DEFAULT_SCHEMA}/tables/{name}"
            )
            self._request("DELETE", table_path)

            # Fileset (SDK). Idempotent: NoSuch* → _Idempotent → no-op.
            client = self._ensure_client()
            if client is not None:
                try:
                    from gravitino import NameIdentifier

                    catalog = client.load_catalog(_FILESET_CATALOG)
                    ident = NameIdentifier.of(_DEFAULT_SCHEMA, name)
                    self._call_sdk(
                        lambda: catalog.as_fileset_catalog().drop_fileset(ident)
                    )
                except _Idempotent:
                    pass
                except GravitinoRequestError as exc:
                    logger.warning(
                        "gravitino_fileset_drop_failed", name=name, error=str(exc)
                    )
            logger.info("gravitino_dataset_deregistered", name=name)

    def sync_outbound(self, entries: list[dict[str, Any]]) -> int:
        """Push local catalog entries to Gravitino."""
        synced = 0
        for entry in entries:
            try:
                self.register_dataset(
                    name=entry["name"],
                    location=entry.get("location", ""),
                    schema=entry.get("schema"),
                )
                synced += 1
            except Exception as exc:
                logger.warning(
                    "gravitino_sync_outbound_failed",
                    name=entry.get("name"),
                    error=str(exc),
                )
        logger.info("gravitino_sync_outbound", synced=synced, total=len(entries))
        return synced

    def sync_inbound(self) -> list[dict[str, Any]]:
        """Pull filesets from Gravitino (SDK list_filesets)."""
        entries: list[dict[str, Any]] = []
        with self._lock:
            client = self._ensure_client()
            if client is None:
                return entries
            try:
                from gravitino.namespace import Namespace

                catalog = client.load_catalog(_FILESET_CATALOG)
                # Bound FilesetCatalog expects a 1-level namespace (schema only);
                # passing metalake.catalog.schema raises "must have 1 level".
                ns = Namespace.of(_DEFAULT_SCHEMA)
                idents = self._call_sdk(
                    lambda: catalog.as_fileset_catalog().list_filesets(ns)
                )
                for ident in idents or []:
                    nm = ident.name()
                    entries.append({
                        "name": nm,
                        "location": f"gravitino://{_FILESET_CATALOG}/{_DEFAULT_SCHEMA}/{nm}",
                    })
            except _Idempotent:
                pass
            except GravitinoRequestError as exc:
                logger.warning("gravitino_sync_inbound_failed", error=str(exc))
        logger.info("gravitino_sync_inbound", count=len(entries))
        return entries

    def get_table_statistics(self, name: str) -> dict[str, Any] | None:
        """Fetch fileset details from Gravitino (SDK load_fileset)."""
        with self._lock:
            client = self._ensure_client()
            if client is None:
                return None
            try:
                from gravitino import NameIdentifier

                catalog = client.load_catalog(_FILESET_CATALOG)
                ident = NameIdentifier.of(_DEFAULT_SCHEMA, name)
                fs = self._call_sdk(
                    lambda: catalog.as_fileset_catalog().load_fileset(ident)
                )
            except _Idempotent:
                return None  # not found
            except GravitinoRequestError as exc:
                logger.warning(
                    "gravitino_stats_fetch_failed", name=name, error=str(exc)
                )
                return None
            if fs is None:
                return None
            ftype = fs.fileset_type()
            return {
                "name": fs.name() if callable(getattr(fs, "name", None)) else name,
                "type": str(ftype) if ftype is not None else "",
                "properties": fs.properties() or {},
            }

    def health(self) -> tuple[str, bool]:
        """Check Gravitino connectivity.

        A health check must never raise — any failure (Gravitino errors,
        auth errors, JSON decode errors, surprise runtime errors) is
        translated into an ``unhealthy`` status for the caller.
        """
        try:
            result = self._request("GET", f"/api/metalakes/{self._metalake}")
        except Exception as exc:
            return (f"unhealthy: {exc}", False)
        if result is not None:
            return ("healthy", True)
        return ("unhealthy", False)
