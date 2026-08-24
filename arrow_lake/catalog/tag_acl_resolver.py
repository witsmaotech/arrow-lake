"""Tag-aware ACL resolver — bridges Gravitino tags to Arrow Lake PermissionChecker ACLs."""

from __future__ import annotations

from typing import Any

import structlog

from arrow_lake.config.gravitino import GravitinoConfig

logger = structlog.get_logger(__name__)


class _TagFetchUnavailable(Exception):
    """Gravitino tag fetch failed for a table (keep previous ACL state)."""


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
        # (table, role) pairs whose current ACL we installed — only these are
        # eligible for removal when their tags lift (v1.10.7 WP6, review H12:
        # manual admin-configured ACLs must never be reaped by the syncer).
        self._tag_derived: set[tuple[str, str]] = set()

    def sync_tags_to_acls(self) -> int:
        """Read all tables from Gravitino, resolve column tags, sync ACLs.

        Returns the number of ACL entries created. Also REMOVES tag-derived
        ACLs whose restrictions no longer apply (tag lifted) — the old
        one-way sync left columns hidden forever (review H12).
        """
        if not self._rules:
            return 0

        tables = self._list_gravitino_tables()
        if not tables:
            return 0

        count = 0
        desired: set[tuple[str, str]] = set()
        for table_name in tables:
            try:
                n, keys = self._sync_table(table_name)
                count += n
                desired |= keys
            except _TagFetchUnavailable:
                # Gravitino hiccup on THIS table: keep last-known state —
                # treating a failed fetch as "no tags" would strip protections
                logger.warning("tag_acl_resolver.tag_fetch_unavailable",
                               table=table_name, exc_info=True)
                desired |= {k for k in self._tag_derived if k[0] == table_name}
            except Exception:
                logger.warning("tag_acl_resolver.table_sync_failed",
                               table=table_name, exc_info=True)
                desired |= {k for k in self._tag_derived if k[0] == table_name}

        # Recovery direction: tag-derived ACLs not desired this round go away.
        for key in self._tag_derived - desired:
            table, role = key
            try:
                if self._checker.delete_acl(table, role):
                    logger.info("tag_acl_resolver.acl_removed", table=table, role=role)
            except Exception:
                logger.debug("tag_acl_resolver.remove_acl_failed",
                             table=table, role=role)
        self._tag_derived = desired

        logger.info("tag_acl_resolver.sync_complete", tables=len(tables), acls=count)
        return count

    def _sync_table(self, table_name: str) -> tuple[int, set[tuple[str, str]]]:
        """Resolve tags for one table and set ACLs.

        Returns (acl_count, tag_derived_keys). Raises _TagFetchUnavailable
        when the tag fetch itself failed (distinct from "no tags") so the
        caller keeps the previous round's state instead of lifting ACLs.
        """
        from arrow_lake.api.rbac import DatasetACL

        column_tags = self._fetch_column_tags(table_name)
        if not column_tags:
            return 0, set()

        schema = self._get_table_schema(table_name)
        if not schema:
            return 0, set()

        all_columns = [col["name"] for col in schema]

        # Build per-role visible columns based on each role's visibility rules
        all_roles = {"admin", "editor", "viewer"}
        restricted_columns: dict[str, set[str]] = {}

        for col, tags in column_tags.items():
            for tag in tags:
                rule = self._rules.get(tag)
                if rule is None:
                    continue
                visible_to = set(rule.get("visible_to", []))
                denied_roles = all_roles - visible_to
                for role in denied_roles:
                    restricted_columns.setdefault(role, set()).add(col)

        if not restricted_columns:
            return 0, set()

        count = 0
        keys: set[tuple[str, str]] = set()
        for role, hidden_cols in restricted_columns.items():
            visible = frozenset(c for c in all_columns if c not in hidden_cols)
            try:
                acl = DatasetACL(
                    dataset=table_name,
                    role=role,
                    visible_columns=visible,
                )
                self._checker.set_acl(acl)
                count += 1
            except Exception:
                # Review F3: keep the key in the tag-derived set even when
                # this write failed — the PREVIOUS round's ACL still stands,
                # and dropping the key here would make the recovery loop
                # delete that protection one sync later.
                logger.warning("tag_acl_resolver.set_acl_failed",
                               table=table_name, role=role, exc_info=True)
            keys.add((table_name, role))

        return count, keys

    def _list_gravitino_tables(self) -> list[str]:
        """List table names from Gravitino REST API."""
        try:
            import json
            from urllib.request import Request, urlopen

            url = (
                f"{self._config.uri}/api/metalakes/{self._config.metalake}"
                f"/catalogs/{self._config.lance_catalog_name}"
                f"/schemas/{self._config.lance_schema_name}/tables"
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
        """Fetch column-level tags for a table via Gravitino SDK.

        v1.10.7 WP6: a failed fetch raises (caller keeps last-known ACLs) —
        the old blanket ``return {}`` was indistinguishable from "no tags"
        and would have lifted restrictions on a transient error once the
        recovery direction landed.
        """
        from arrow_lake.quality.gravitino_tags import GravitinoTagService

        svc = GravitinoTagService(self._config)
        return svc.list_column_tags(table_name, strict=True)

    def _get_table_schema(self, table_name: str) -> list[dict[str, Any]]:
        """Get table column schema from Gravitino.

        Raises on failure (review F2, 2026-08-24): the old blanket
        ``return []`` was indistinguishable from "no schema", so a transient
        schema-endpoint error made the table contribute no desired keys and
        the recovery loop deleted its still-valid ACLs. The caller keeps
        last-known state for this table.
        """
        import json
        from urllib.request import Request, urlopen

        url = (
            f"{self._config.uri}/api/metalakes/{self._config.metalake}"
            f"/catalogs/{self._config.lance_catalog_name}"
            f"/schemas/{self._config.lance_schema_name}/tables/{table_name}"
        )
        req = Request(url)
        req.add_header("Accept", "application/vnd.gravitino.v1+json")
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return data.get("table", {}).get("columns", [])
