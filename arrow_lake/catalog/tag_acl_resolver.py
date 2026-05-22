"""Tag-aware ACL resolver — bridges Gravitino tags to Arrow Lake PermissionChecker ACLs."""

from __future__ import annotations

import threading
from typing import Any

import structlog

from arrow_lake.config.gravitino import GravitinoConfig

logger = structlog.get_logger(__name__)


class TagAwareACLResolver:
    """Periodically reads Gravitino column-level tags and syncs them into the local
    ``PermissionChecker`` as column-level ACLs.

    Tag → role mapping is configured via ``GravitinoConfig.tag_access_rules``::

        tag_access_rules = {
            "pii":       {"visible_to": ["admin"]},
            "sensitive": {"visible_to": ["admin", "editor"]},
        }

    Columns tagged with ``pii`` will only be visible to admin; other roles will have
    those columns excluded from query results via the existing ``apply_table_filter`` pipeline.
    """

    def __init__(self, config: GravitinoConfig, checker: Any) -> None:
        self._config = config
        self._checker = checker
        self._rules = config.tag_access_rules
        self._lock = threading.Lock()

    def sync_tags_to_acls(self) -> int:
        """Read all tables from Gravitino, resolve column tags, inject ACLs into PermissionChecker.

        Returns the number of ACL entries created.
        """
        if not self._rules:
            return 0

        tables = self._list_gravitino_tables()
        if not tables:
            return 0

        count = 0
        for table_name in tables:
            try:
                n = self._sync_table(table_name)
                count += n
            except Exception:
                logger.warning("tag_acl_resolver.table_sync_failed",
                               table=table_name, exc_info=True)
        logger.info("tag_acl_resolver.sync_complete", tables=len(tables), acls=count)
        return count

    def _sync_table(self, table_name: str) -> int:
        """Resolve tags for one table and set ACLs. Returns number of ACL entries set."""
        column_tags = self._fetch_column_tags(table_name)
        if not column_tags:
            return 0

        # Build role → visible_columns mapping
        schema = self._get_table_schema(table_name)
        if not schema:
            return 0

        all_columns = [col["name"] for col in schema]
        restricted_columns: set[str] = set()

        for col, tags in column_tags.items():
            for tag in tags:
                rule = self._rules.get(tag)
                if rule is None:
                    continue
                restricted_columns.add(col)

        if not restricted_columns:
            return 0

        visible = [c for c in all_columns if c not in restricted_columns]
        roles_needing_filter: set[str] = set()

        # Determine which roles need column filtering
        for col, tags in column_tags.items():
            for tag in tags:
                rule = self._rules.get(tag)
                if rule:
                    all_roles = {"admin", "editor", "viewer"}
                    visible_to = set(rule.get("visible_to", []))
                    roles_needing_filter.update(all_roles - visible_to)

        for role in roles_needing_filter:
            try:
                self._checker.set_acl(table_name, role,
                                      visible_columns=visible, row_filter=None)
            except Exception:
                logger.debug("tag_acl_resolver.set_acl_failed",
                             table=table_name, role=role)

        return len(roles_needing_filter)

    def _list_gravitino_tables(self) -> list[str]:
        """List table names from Gravitino REST API."""
        try:
            import json
            from urllib.request import Request, urlopen

            url = (
                f"{self._config.uri}/api/metalakes/{self._config.metalake}"
                f"/catalogs/lance-catalog/schemas/arrow_lake/tables"
            )
            req = Request(url)
            req.add_header("Accept", "application/vnd.gravitino.v1+json")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            return [i["name"] for i in data.get("identifiers", [])]
        except Exception:
            logger.debug("tag_acl_resolver.list_tables_failed", exc_info=True)
            return []

    def _fetch_column_tags(self, table_name: str) -> dict[str, list[str]]:
        """Fetch column-level tags for a table via Gravitino SDK."""
        try:
            from arrow_lake.quality.gravitino_tags import GravitinoTagService

            svc = GravitinoTagService(self._config)
            return svc.list_column_tags(table_name)
        except Exception:
            return {}

    def _get_table_schema(self, table_name: str) -> list[dict[str, Any]]:
        """Get table column schema from Gravitino."""
        try:
            import json
            from urllib.request import Request, urlopen

            url = (
                f"{self._config.uri}/api/metalakes/{self._config.metalake}"
                f"/catalogs/lance-catalog/schemas/arrow_lake/tables/{table_name}"
            )
            req = Request(url)
            req.add_header("Accept", "application/vnd.gravitino.v1+json")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            return data.get("table", {}).get("columns", [])
        except Exception:
            return []
