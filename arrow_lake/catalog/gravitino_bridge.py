"""Bidirectional sync between DuckDB catalog_tables and Gravitino.

Registers datasets as Gravitino Tables (lance-catalog) and Filesets
(minio-fileset catalog) for metadata tracking. Tables carry column
schema and appear in the Gravitino Tables UI.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import structlog

from arrow_lake.catalog.gravitino_auth import create_auth_provider
from arrow_lake.config.gravitino import GravitinoConfig

logger = structlog.get_logger(__name__)

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


def _arrow_type_to_gravitino(arrow_type: Any) -> str:
    """Convert a PyArrow type to a Gravitino type string."""
    type_str = str(arrow_type)

    if type_str in _PRIMITIVE_MAP:
        return _PRIMITIVE_MAP[type_str]

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

    Uses Gravitino REST API directly (via urllib) for fileset management.
    Thread-safe: all REST interactions are serialized via a lock.
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

    @property
    def enabled(self) -> bool:
        return True

    def _request(
        self, method: str, path: str, body: dict | None = None
    ) -> dict[str, Any] | None:
        """Send an authenticated request to Gravitino REST API."""
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
            logger.warning("gravitino_http_error", method=method, path=path, code=e.code)
            return None
        except (URLError, OSError) as exc:
            logger.warning("gravitino_request_failed", method=method, path=path, error=str(exc))
            return None

    def _ensure_schema(self) -> None:
        """Idempotently ensure the default schema exists in both catalogs."""
        if self._schema_ready:
            return
        for catalog in (_LANCE_CATALOG, _FILESET_CATALOG):
            schema_path = (
                f"/api/metalakes/{self._metalake}"
                f"/catalogs/{catalog}/schemas"
            )
            self._request("POST", schema_path, {
                "name": _DEFAULT_SCHEMA,
                "comment": "Arrow Lake datasets",
            })
        self._schema_ready = True

    def register_dataset(
        self, name: str, schema: Any = None, location: str = ""
    ) -> None:
        """Register a dataset in Gravitino as a Table with schema + a Fileset."""
        with self._lock:
            self._ensure_schema()
            if not location:
                location = f"s3a://arrow-lake/{name}.lance"

            # 1. Register as Table in lance-catalog (with columns)
            self._register_table(name, schema, location)

            # 2. Register as Fileset in minio-fileset (path tracking)
            fileset_path = (
                f"/api/metalakes/{self._metalake}"
                f"/catalogs/{_FILESET_CATALOG}"
                f"/schemas/{_DEFAULT_SCHEMA}/filesets"
            )
            s3_location = location.replace("s3a://", "s3://")
            result = self._request("POST", fileset_path, {
                "name": name,
                "comment": f"Arrow Lake dataset: {name}",
                "type": "MANAGED",
                "storageLocations": {"default": s3_location},
                "properties": {},
            })
            if result is not None:
                logger.info("gravitino_fileset_registered", name=name)
            else:
                logger.info("gravitino_fileset_exists", name=name)

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
            table_path = (
                f"/api/metalakes/{self._metalake}"
                f"/catalogs/{_LANCE_CATALOG}"
                f"/schemas/{_DEFAULT_SCHEMA}/tables/{name}"
            )
            self._request("DELETE", table_path)
            fileset_path = (
                f"/api/metalakes/{self._metalake}"
                f"/catalogs/{_FILESET_CATALOG}"
                f"/schemas/{_DEFAULT_SCHEMA}/filesets/{name}"
            )
            self._request("DELETE", fileset_path)
            logger.info("gravitino_dataset_deregistered", name=name)

    def sync_outbound(self, entries: list[dict[str, Any]]) -> int:
        """Push local catalog entries to Gravitino."""
        synced = 0
        for entry in entries:
            try:
                self.register_dataset(
                    name=entry["name"],
                    location=entry.get("location", ""),
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
        """Pull filesets from Gravitino."""
        entries: list[dict[str, Any]] = []
        with self._lock:
            fileset_path = (
                f"/api/metalakes/{self._metalake}"
                f"/catalogs/{_FILESET_CATALOG}"
                f"/schemas/{_DEFAULT_SCHEMA}/filesets"
            )
            result = self._request("GET", fileset_path)
            if result and "identifiers" in result:
                for ident in result["identifiers"]:
                    entries.append({
                        "name": ident.get("name", ""),
                        "location": f"gravitino://{_FILESET_CATALOG}/{_DEFAULT_SCHEMA}/{ident.get('name', '')}",
                    })
        logger.info("gravitino_sync_inbound", count=len(entries))
        return entries

    def get_table_statistics(self, name: str) -> dict[str, Any] | None:
        """Fetch fileset details from Gravitino."""
        with self._lock:
            fileset_path = (
                f"/api/metalakes/{self._metalake}"
                f"/catalogs/{_FILESET_CATALOG}"
                f"/schemas/{_DEFAULT_SCHEMA}/filesets/{name}"
            )
            result = self._request("GET", fileset_path)
            if result and "fileset" in result:
                fs = result["fileset"]
                return {
                    "name": fs.get("name", name),
                    "type": fs.get("type", ""),
                    "properties": fs.get("properties", {}),
                }
            return None

    def health(self) -> tuple[str, bool]:
        """Check Gravitino connectivity."""
        try:
            result = self._request("GET", f"/api/metalakes/{self._metalake}")
            if result is not None:
                return ("healthy", True)
            return ("unhealthy", False)
        except Exception as exc:
            return (f"unhealthy: {exc}", False)
