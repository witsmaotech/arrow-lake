"""Unified Gravitino SDK wrapper with error handling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class GravitinoTableInfo:
    """Lightweight table info from Gravitino."""

    name: str
    catalog: str
    schema: str
    columns: tuple[dict[str, Any], ...]
    properties: tuple[tuple[str, str], ...] = ()


class ArrowLakeGravitinoClient:
    """Thin wrapper around the Gravitino Python SDK.

    All methods catch exceptions and return None/empty on failure.
    The SDK is imported lazily so the module loads without it installed.
    """

    def __init__(self, uri: str, metalake: str) -> None:
        self._uri = uri
        self._metalake = metalake
        self._admin_client: Any = None
        self._client: Any = None
        self._catalog_name = "arrow_lake_lance"
        self._schema_name = "default"
        self._initialized = False

    def _ensure_initialized(self) -> bool:
        if self._initialized:
            return self._client is not None
        self._initialized = True
        try:
            from gravitino.client.gravitino_admin_client import (
                GravitinoAdminClient,  # type: ignore[import-untyped]
            )
            from gravitino.client.gravitino_client import (
                GravitinoClient,  # type: ignore[import-untyped]
            )

            self._admin_client = GravitinoAdminClient(uri=self._uri)
            self._admin_client.load_metalake(self._metalake)
            self._client = GravitinoClient(
                uri=self._uri, metalake_name=self._metalake
            )
            logger.info(
                "gravitino_client_initialized",
                uri=self._uri,
                metalake=self._metalake,
            )
            return True
        except Exception as exc:
            logger.warning("gravitino_client_init_failed", error=str(exc))
            self._client = None
            return False

    def list_catalogs(self) -> list[str]:
        if not self._ensure_initialized():
            return []
        try:
            catalogs = self._admin_client.list_catalogs(self._metalake)
            return [c.name() for c in catalogs]
        except Exception as exc:
            logger.warning("gravitino_list_catalogs_failed", error=str(exc))
            return []

    def create_catalog(
        self, name: str, provider: str = "lance", properties: dict[str, str] | None = None
    ) -> bool:
        if not self._ensure_initialized():
            return False
        try:

            self._admin_client.create_catalog(
                metalake_name=self._metalake,
                name=name,
                catalog_type="relational",
                provider=provider,
                comment="Arrow Lake managed catalog",
                properties=properties or {},
            )
            logger.info("gravitino_catalog_created", name=name)
            return True
        except Exception as exc:
            logger.warning("gravitino_create_catalog_failed", error=str(exc))
            return False

    def list_tables(self, catalog: str | None = None, schema: str | None = None) -> list[str]:
        if not self._ensure_initialized():
            return []
        try:
            names = self._client.load_catalog(
                catalog or self._catalog_name
            ).as_table_catalog().list_tables(
                schema or self._schema_name
            )
            return list(names) if names else []
        except Exception as exc:
            logger.warning("gravitino_list_tables_failed", error=str(exc))
            return []

    def load_table(
        self, name: str, catalog: str | None = None, schema: str | None = None
    ) -> GravitinoTableInfo | None:
        if not self._ensure_initialized():
            return None
        try:
            table = self._client.load_catalog(
                catalog or self._catalog_name
            ).as_table_catalog().load_table(
                schema or self._schema_name, name
            )
            cols = tuple(
                {
                    "name": col.name(),
                    "type": str(col.data_type()),
                    "nullable": col.nullable(),
                }
                for col in table.columns()
            )
            props = table.properties() or {}
            return GravitinoTableInfo(
                name=table.name(),
                catalog=catalog or self._catalog_name,
                schema=schema or self._schema_name,
                columns=cols,
                properties=tuple(sorted(props.items())),
            )
        except Exception as exc:
            logger.warning("gravitino_load_table_failed", name=name, error=str(exc))
            return None

    def drop_table(
        self, name: str, catalog: str | None = None, schema: str | None = None
    ) -> bool:
        if not self._ensure_initialized():
            return False
        try:
            self._client.load_catalog(
                catalog or self._catalog_name
            ).as_table_catalog().purge_table(
                schema or self._schema_name, name
            )
            logger.info("gravitino_table_dropped", name=name)
            return True
        except Exception as exc:
            logger.warning("gravitino_drop_table_failed", name=name, error=str(exc))
            return False

    def health(self) -> tuple[str, bool]:
        try:
            if not self._ensure_initialized():
                return ("unavailable", False)
            self._admin_client.list_metalakes()
            return ("healthy", True)
        except Exception as exc:
            logger.warning("gravitino_health_check_failed", error=str(exc))
            return (f"unhealthy: {exc}", False)
