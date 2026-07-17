"""RbacStore — relational persistence for the four RBAC in-memory dicts.

Owns the tables created by ``V001__init_rbac.sql``:

* ``dataset_acl_grants``   ← ``PermissionChecker._dataset_acls``
* ``dataset_row_col_acls`` ← ``PermissionChecker._row_col_acls`` (DatasetACL)
* ``schema_acls``          ← ``PermissionChecker._schema_acls`` (SchemaACL)
* ``acl_denies``           ← ``PermissionChecker._deny_list``
* ``role_permissions``     ← seeded from ``_ROLE_PERMISSIONS`` at startup

Reads are served through a short-TTL cache so the hot ACL-resolution path
does not hit sqld on every request; every mutator invalidates the affected
cache key on its own worker.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from arrow_lake.system_db.connection import SystemDB, SystemDBError
from arrow_lake.system_db.stores.base import FailMode, TTLCache

logger = structlog.get_logger(__name__)


def _loads_array(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        return list(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return []


class RbacStore:
    """Persistence backend for :class:`~arrow_lake.api.rbac.PermissionChecker`.

    Security-sensitive: uses ``FailMode.FAIL_CLOSE``. When the DB is
    unreachable, callers should treat the result as "deny" rather than fall
    back to an open default.
    """

    fail_mode = FailMode.FAIL_CLOSE

    def __init__(self, db: SystemDB, *, cache_ttl: float = 5.0) -> None:
        self._db = db
        self._cache = TTLCache(cache_ttl)

    # ------------------------------------------------------------------
    # role → permission matrix (seeded at startup)
    # ------------------------------------------------------------------
    def seed_role_permissions(
        self, matrix: dict[str, frozenset[str] | set[str]]
    ) -> int:
        """Idempotently seed default role→permissions. Returns rows inserted."""
        rows = [(role, perm) for role, perms in matrix.items() for perm in perms]
        if not rows:
            return 0
        with self._db.with_write() as db:
            before = db.execute("SELECT COUNT(*) FROM role_permissions").fetchone()[0]
            db.executemany(
                "INSERT OR IGNORE INTO role_permissions (role, permission) VALUES (?, ?)",
                rows,
            )
            after = db.execute("SELECT COUNT(*) FROM role_permissions").fetchone()[0]
        self._cache.invalidate("role_perms")
        return int(after) - int(before)

    def get_role_permissions(self, role: str) -> frozenset[str]:
        cur = self._db.execute(
            "SELECT permission FROM role_permissions WHERE role = ?", (role,)
        )
        rows = cur.fetchall() if cur is not None else []
        return frozenset(r[0] for r in rows)

    # ------------------------------------------------------------------
    # dataset → role → action grants  (_dataset_acls)
    # ------------------------------------------------------------------
    def get_dataset_grants(self, dataset: str) -> dict[str, set[str]]:
        cache_key = f"grants:{dataset}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        cur = self._db.execute(
            "SELECT role, action FROM dataset_acl_grants WHERE dataset_name = ?",
            (dataset,),
        )
        rows = cur.fetchall() if cur is not None else []
        out: dict[str, set[str]] = {}
        for role, action in rows:
            out.setdefault(role, set()).add(action)
        self._cache.set(cache_key, out)
        return out

    def grant_dataset_access(self, dataset: str, role: str, action: str) -> None:
        with self._db.with_write() as db:
            db.execute(
                "INSERT OR IGNORE INTO dataset_acl_grants "
                "(dataset_name, role, action) VALUES (?, ?, ?)",
                (dataset, role, action),
            )
        self._cache.invalidate(f"grants:{dataset}")

    def revoke_dataset_access(self, dataset: str, role: str) -> int:
        with self._db.with_write() as db:
            cur = db.execute(
                "DELETE FROM dataset_acl_grants WHERE dataset_name = ? AND role = ?",
                (dataset, role),
            )
            count = cur.rowcount if cur is not None else 0
        self._cache.invalidate(f"grants:{dataset}")
        return int(count)

    # ------------------------------------------------------------------
    # dataset row/column-level ACL  (_row_col_acls → DatasetACL)
    # ------------------------------------------------------------------
    def set_row_col_acl(
        self,
        dataset: str,
        role: str,
        *,
        visible_columns: list[str] | None = None,
        row_filter: str = "",
        denied_actions: list[str] | None = None,
    ) -> None:
        vc = json.dumps(visible_columns) if visible_columns else None
        da = json.dumps(denied_actions) if denied_actions else None
        with self._db.with_write() as db:
            db.execute(
                "INSERT INTO dataset_row_col_acls "
                "(dataset_name, role, visible_columns, row_filter, denied_actions) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (dataset_name, role) DO UPDATE SET "
                "visible_columns = excluded.visible_columns, "
                "row_filter = excluded.row_filter, "
                "denied_actions = excluded.denied_actions",
                (dataset, role, vc, row_filter, da),
            )
        self._cache.invalidate(f"rowcol:{dataset}")

    def get_row_col_acl(self, dataset: str, role: str) -> dict[str, Any] | None:
        for acl in self.list_row_col_acls(dataset):
            if acl["role"] == role:
                return acl
        return None

    def list_row_col_acls(self, dataset: str) -> list[dict[str, Any]]:
        cache_key = f"rowcol:{dataset}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        cur = self._db.execute(
            "SELECT role, visible_columns, row_filter, denied_actions "
            "FROM dataset_row_col_acls WHERE dataset_name = ?",
            (dataset,),
        )
        rows = cur.fetchall() if cur is not None else []
        out = [
            {
                "dataset": dataset,
                "role": role,
                "visible_columns": frozenset(_loads_array(vc)),
                "row_filter": rf or "",
                "denied_actions": frozenset(_loads_array(da)),
            }
            for role, vc, rf, da in rows
        ]
        self._cache.set(cache_key, out)
        return out

    def delete_row_col_acl(self, dataset: str, role: str) -> bool:
        with self._db.with_write() as db:
            cur = db.execute(
                "DELETE FROM dataset_row_col_acls "
                "WHERE dataset_name = ? AND role = ?",
                (dataset, role),
            )
            deleted = cur is not None and cur.rowcount > 0
        self._cache.invalidate(f"rowcol:{dataset}")
        return bool(deleted)

    # ------------------------------------------------------------------
    # schema-level ACL  (_schema_acls → SchemaACL)
    # ------------------------------------------------------------------
    def set_schema_acl(
        self,
        schema: str,
        role: str,
        *,
        allowed_actions: list[str] | None = None,
        denied_actions: list[str] | None = None,
    ) -> None:
        aa = json.dumps(allowed_actions) if allowed_actions else None
        da = json.dumps(denied_actions) if denied_actions else None
        with self._db.with_write() as db:
            db.execute(
                "INSERT INTO schema_acls "
                "(schema_name, role, allowed_actions, denied_actions) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (schema_name, role) DO UPDATE SET "
                "allowed_actions = excluded.allowed_actions, "
                "denied_actions = excluded.denied_actions",
                (schema, role, aa, da),
            )
        self._cache.invalidate(f"schema:{schema}")

    def get_schema_acl(self, schema: str, role: str) -> dict[str, Any] | None:
        for acl in self.list_schema_acls(schema):
            if acl["role"] == role:
                return acl
        return None

    def list_schema_acls(self, schema: str) -> list[dict[str, Any]]:
        cache_key = f"schema:{schema}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        cur = self._db.execute(
            "SELECT role, allowed_actions, denied_actions "
            "FROM schema_acls WHERE schema_name = ?",
            (schema,),
        )
        rows = cur.fetchall() if cur is not None else []
        out = [
            {
                "schema": schema,
                "role": role,
                "allowed_actions": frozenset(_loads_array(aa)),
                "denied_actions": frozenset(_loads_array(da)),
            }
            for role, aa, da in rows
        ]
        self._cache.set(cache_key, out)
        return out

    def delete_schema_acl(self, schema: str, role: str) -> bool:
        with self._db.with_write() as db:
            cur = db.execute(
                "DELETE FROM schema_acls WHERE schema_name = ? AND role = ?",
                (schema, role),
            )
            deleted = cur is not None and cur.rowcount > 0
        self._cache.invalidate(f"schema:{schema}")
        return bool(deleted)

    # ------------------------------------------------------------------
    # deny-list  (_deny_list)
    # ------------------------------------------------------------------
    def list_denies(self, dataset: str) -> set[str]:
        cache_key = f"deny:{dataset}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        cur = self._db.execute(
            "SELECT action FROM acl_denies WHERE dataset_name = ?", (dataset,)
        )
        rows = cur.fetchall() if cur is not None else []
        out = {r[0] for r in rows}
        self._cache.set(cache_key, out)
        return out

    def deny_action(self, dataset: str, action: str, reason: str = "") -> None:
        with self._db.with_write() as db:
            db.execute(
                "INSERT OR IGNORE INTO acl_denies (dataset_name, action, reason) "
                "VALUES (?, ?, ?)",
                (dataset, action, reason),
            )
        self._cache.invalidate(f"deny:{dataset}")

    def remove_deny(self, dataset: str, action: str) -> bool:
        with self._db.with_write() as db:
            cur = db.execute(
                "DELETE FROM acl_denies WHERE dataset_name = ? AND action = ?",
                (dataset, action),
            )
            deleted = cur is not None and cur.rowcount > 0
        self._cache.invalidate(f"deny:{dataset}")
        return bool(deleted)

    # ------------------------------------------------------------------
    def invalidate_all(self) -> None:
        """Drop every cached ACL (called after bulk changes)."""
        self._cache.invalidate()


# Re-exported for callers that want a single failure type to catch.
__all__ = ["RbacStore", "SystemDBError"]
