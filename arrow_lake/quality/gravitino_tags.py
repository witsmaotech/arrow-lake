"""Gravitino tag service for data governance."""

from __future__ import annotations

from gravitino import NameIdentifier

import threading
from typing import Any

import structlog

from arrow_lake.config.gravitino import GravitinoConfig

logger = structlog.get_logger(__name__)


class GravitinoTagService:
    """Manage Gravitino tags for data classification and governance.

    Wraps the Gravitino SDK Tag API. All calls degrade gracefully if
    Gravitino is unavailable.

    Args:
        config: Gravitino connection config.
    """

    SENSITIVE = "sensitive"
    PII = "pii"
    FINANCIAL = "financial"
    EXPIRES_30D = "expires:30d"

    def __init__(self, config: GravitinoConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._client: Any = None
        if config.enabled:
            self._init_client()

    def _init_client(self) -> None:
        try:
            from gravitino.client.gravitino_client import (
                GravitinoClient,  # type: ignore[import-untyped]
            )

            self._client = GravitinoClient(
                uri=self._config.uri,
                metalake_name=self._config.metalake,
            )
        except Exception as exc:
            logger.warning("gravitino_tag_client_init_failed", error=str(exc))

    def _get_metalake(self) -> Any:
        if self._client is None:
            return None
        try:
            return self._client.load_metalake(self._config.metalake)
        except Exception as exc:
            logger.warning("gravitino_load_metalake_failed", error=str(exc))
            return None

    def create_tag(self, name: str, comment: str = "") -> None:
        """Create a tag in Gravitino."""
        metalake = self._get_metalake()
        if metalake is None:
            return
        with self._lock:
            try:
                metalake.create_tag(name=name, comment=comment)
                logger.info("gravitino_tag_created", name=name)
            except Exception as exc:
                logger.warning("gravitino_create_tag_failed", name=name, error=str(exc))

    def tag_table(self, table: str, tags: list[str]) -> None:
        """Associate tags with a table."""
        metalake = self._get_metalake()
        if metalake is None:
            return
        with self._lock:
            try:
                catalog = self._client.load_catalog(self._config.lance_catalog_name)
                table_obj = catalog.as_table_catalog().load_table(NameIdentifier.of("default", table))
                for tag_name in tags:
                    table_obj.supports_tags().associate_tags([tag_name])
                logger.info("gravitino_table_tagged", table=table, tags=tags)
            except Exception as exc:
                logger.warning("gravitino_tag_table_failed", table=table, error=str(exc))

    def tag_column(self, table: str, column: str, tags: list[str]) -> None:
        """Associate tags with a specific column."""
        metalake = self._get_metalake()
        if metalake is None:
            return
        with self._lock:
            try:
                catalog = self._client.load_catalog(self._config.lance_catalog_name)
                table_obj = catalog.as_table_catalog().load_table(NameIdentifier.of("default", table))
                for tag_name in tags:
                    table_obj.supports_tags().associate_column_tags(column, [tag_name])
                logger.info(
                    "gravitino_column_tagged", table=table, column=column, tags=tags
                )
            except Exception as exc:
                logger.warning(
                    "gravitino_tag_column_failed",
                    table=table,
                    column=column,
                    error=str(exc),
                )

    def list_tags(self, table: str) -> list[str]:
        """List tags associated with a table."""
        metalake = self._get_metalake()
        if metalake is None:
            return []
        with self._lock:
            try:
                catalog = self._client.load_catalog(self._config.lance_catalog_name)
                table_obj = catalog.as_table_catalog().load_table(NameIdentifier.of("default", table))
                tag_objs = table_obj.supports_tags().list_tags()
                return [t.name() for t in (tag_objs or [])]
            except Exception as exc:
                logger.warning("gravitino_list_tags_failed", table=table, error=str(exc))
                return []

    def list_column_tags(self, table: str) -> dict[str, list[str]]:
        """List column-level tags for a table. Returns {column_name: [tag_names]}."""
        metalake = self._get_metalake()
        if metalake is None:
            return {}
        with self._lock:
            try:
                catalog = self._client.load_catalog(self._config.lance_catalog_name)
                table_obj = catalog.as_table_catalog().load_table(NameIdentifier.of("default", table))
                columns = table_obj.columns()
                result: dict[str, list[str]] = {}
                for col in (columns or []):
                    col_name = col.name() if hasattr(col, "name") else str(col)
                    try:
                        tag_objs = table_obj.supports_tags().list_column_tags(col_name)
                        tags = [t.name() for t in (tag_objs or [])]
                        if tags:
                            result[col_name] = tags
                    except Exception:
                        pass
                return result
            except Exception as exc:
                logger.warning("gravitino_list_column_tags_failed", table=table, error=str(exc))
                return {}

    def get_tables_by_tag(self, tag: str) -> list[str]:
        """Find all tables with a given tag."""
        metalake = self._get_metalake()
        if metalake is None:
            return []
        with self._lock:
            try:
                tag_obj = metalake.get_tag(tag)
                if tag_obj is None:
                    return []
                objects = metalake.list_tags_associated_objects(tag)
                return [
                    obj.name()
                    for obj in (objects or [])
                    if hasattr(obj, "name")
                ]
            except Exception as exc:
                logger.warning("gravitino_get_tables_by_tag_failed", tag=tag, error=str(exc))
                return []
