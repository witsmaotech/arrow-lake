"""Gravitino tag service for data governance."""

from __future__ import annotations

from gravitino import NameIdentifier

import threading
import time
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

    # A table that appears in the REST listing but cannot be loaded by the catalog
    # provider (e.g. NoSuchTable for image_search_real) is cached so the periodic
    # tag→ACL sync doesn't re-query — and re-log — it every 30s. TTL bounds staleness
    # if the table is later created; monotonic so wall-clock skew can't trick us.
    _MISSING_TTL = 300.0

    def __init__(self, config: GravitinoConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._client: Any = None
        self._missing_cache: dict[str, float] = {}
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
            logger.warning("gravitino_load_metalake_failed", error=self._short_error(exc))
            return None

    @staticmethod
    def _short_error(exc: BaseException) -> str:
        """First line of an exception message — drops verbose Java stack traces.

        Gravitino SDK embeds the full server-side stack (dozens of ``at ...`` frames)
        in ``str(exc)``; logging that verbatim floods the log on every sync cycle.
        """
        msg = str(exc) or type(exc).__name__
        return msg.splitlines()[0][:200]

    @staticmethod
    def _is_missing_table(exc: BaseException) -> bool:
        """True for NoSuchTable / 'does not exist' — expected for un-registered tables."""
        text = f"{type(exc).__name__} {exc}".lower()
        return "nosuchtable" in text or "does not exist" in text

    def _is_known_missing(self, table: str) -> bool:
        seen_at = self._missing_cache.get(table)
        if seen_at is None:
            return False
        if time.monotonic() - seen_at > self._MISSING_TTL:
            self._missing_cache.pop(table, None)
            return False
        return True

    def _mark_missing(self, table: str) -> None:
        self._missing_cache[table] = time.monotonic()

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
        if self._is_known_missing(table):
            return []
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
                if self._is_missing_table(exc):
                    # Expected for tables registered in REST but not loadable by the
                    # catalog provider — skip silently and cache so the next sync cycle
                    # doesn't retry. Don't log the server-side Java stack.
                    self._mark_missing(table)
                    logger.debug("gravitino_list_tags_skipped",
                                 table=table, error=self._short_error(exc))
                else:
                    logger.warning("gravitino_list_tags_failed",
                                   table=table, error=self._short_error(exc))
                return []

    def list_column_tags(self, table: str, *, strict: bool = False) -> dict[str, list[str]]:
        """List column-level tags for a table. Returns {column_name: [tag_names]}.

        ``strict=True`` (v1.10.7 WP6): transient failures RAISE instead of
        returning {} — callers that sync ACLs must distinguish "no tags"
        from "Gravitino unreachable" (the latter must keep prior state, not
        lift restrictions). Known-missing tables still return {} either way.
        """
        if self._is_known_missing(table):
            return {}
        metalake = self._get_metalake()
        if metalake is None:
            if strict:
                raise RuntimeError("gravitino metalake unavailable")
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
                if self._is_missing_table(exc):
                    self._mark_missing(table)
                    logger.debug("gravitino_list_column_tags_skipped",
                                 table=table, error=self._short_error(exc))
                    return {}
                logger.warning("gravitino_list_column_tags_failed",
                               table=table, error=self._short_error(exc))
                if strict:
                    raise
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
