"""Role-Based Access Control (RBAC) permission system.

Defines role-permission matrix, dataset-level ACLs, row/column-level ACLs,
and PermissionChecker for enforcing access control in API endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.compute as pc
import structlog

if TYPE_CHECKING:
    from arrow_lake.api.auth_models import Role

logger = structlog.get_logger(__name__)


class Permission(StrEnum):
    """Permission identifiers for RBAC."""

    DATASET_READ = "dataset:read"
    DATASET_WRITE = "dataset:write"
    DATASET_DELETE = "dataset:delete"
    ADMIN_MANAGE = "admin:manage"


# Role-permission matrix: ADMIN > EDITOR > VIEWER
_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({
        Permission.DATASET_READ,
    }),
    "editor": frozenset({
        Permission.DATASET_READ,
        Permission.DATASET_WRITE,
        Permission.DATASET_DELETE,
    }),
    "admin": frozenset(Permission),
}

_ADMIN_ROLE = "admin"


@dataclass(frozen=True)
class DatasetACL:
    """Row/column-level access control for a dataset.

    Attributes:
        dataset: Dataset name.
        role: Role this ACL applies to.
        visible_columns: Columns this role can see (empty = all).
        row_filter: SQL-style WHERE expression for row filtering (empty = all rows).
    """

    dataset: str
    role: str
    visible_columns: frozenset[str] = frozenset()
    row_filter: str = ""


class PermissionChecker:
    """Evaluate role-permission matrix and dataset-level ACLs.

    In-memory ACL storage — sufficient for single-team deployments.
    Interface designed for trivial upgrade to database-backed store.
    """

    def __init__(self) -> None:
        # dataset -> role -> set of granted actions
        self._dataset_acls: dict[str, dict[str, set[str]]] = {}
        # dataset -> role -> DatasetACL (row/column level)
        self._row_col_acls: dict[str, dict[str, DatasetACL]] = {}

    def has_permission(self, role: str | Role, perm: str) -> bool:
        """Check if a role has a specific permission."""
        role_name = role if isinstance(role, str) else role.value
        perms = _ROLE_PERMISSIONS.get(role_name, frozenset())
        return perm in perms

    def get_permissions(self, role: str | Role) -> frozenset[str]:
        """Return all permissions for a role."""
        role_name = role if isinstance(role, str) else role.value
        return _ROLE_PERMISSIONS.get(role_name, frozenset())

    def check_dataset_access(
        self, *, role: str | Role, dataset: str, action: str
    ) -> bool:
        """Check if a role can perform an action on a dataset."""
        role_name = role if isinstance(role, str) else role.value

        if role_name == _ADMIN_ROLE:
            return True

        dataset_acl = self._dataset_acls.get(dataset, {})
        role_grants = dataset_acl.get(role_name)
        if role_grants is not None:
            return action in role_grants

        full_perm = f"dataset:{action}"
        return self.has_permission(role_name, full_perm)

    def grant_dataset_access(
        self, dataset: str, role: str | Role, action: str
    ) -> None:
        """Grant a role a specific action on a dataset."""
        role_name = role if isinstance(role, str) else role.value
        if dataset not in self._dataset_acls:
            self._dataset_acls[dataset] = {}
        if role_name not in self._dataset_acls[dataset]:
            self._dataset_acls[dataset][role_name] = set()
        self._dataset_acls[dataset][role_name].add(action)

    def revoke_dataset_access(self, dataset: str, role: str | Role) -> None:
        """Revoke all actions for a role on a dataset."""
        role_name = role if isinstance(role, str) else role.value
        if dataset in self._dataset_acls and role_name in self._dataset_acls[dataset]:
            del self._dataset_acls[dataset][role_name]

    # ------------------------------------------------------------------
    # Row/column ACL management
    # ------------------------------------------------------------------

    def set_acl(self, acl: DatasetACL) -> None:
        """Set row/column ACL for a dataset + role."""
        if acl.dataset not in self._row_col_acls:
            self._row_col_acls[acl.dataset] = {}
        self._row_col_acls[acl.dataset][acl.role] = acl
        logger.info("acl_set", dataset=acl.dataset, role=acl.role)

    def get_acl(self, dataset: str, role: str | Role) -> DatasetACL | None:
        """Get row/column ACL for a dataset + role."""
        role_name = role if isinstance(role, str) else role.value
        return self._row_col_acls.get(dataset, {}).get(role_name)

    def list_acls(self, dataset: str) -> list[DatasetACL]:
        """List all row/column ACLs for a dataset."""
        return list(self._row_col_acls.get(dataset, {}).values())

    def delete_acl(self, dataset: str, role: str | Role) -> bool:
        """Delete row/column ACL for a dataset + role. Returns True if found."""
        role_name = role if isinstance(role, str) else role.value
        acls = self._row_col_acls.get(dataset)
        if acls and role_name in acls:
            del acls[role_name]
            logger.info("acl_deleted", dataset=dataset, role=role_name)
            return True
        return False

    # ------------------------------------------------------------------
    # ACL-based table filtering
    # ------------------------------------------------------------------

    def apply_table_filter(
        self, table: pa.Table, *, dataset: str, role: str | Role,
    ) -> pa.Table:
        """Apply row/column ACL filtering and masking to a PyArrow table.

        Admin role bypasses all filtering and masking.
        If no ACL is configured for the role+dataset, returns the table unchanged
        (but masking may still apply if Gravitino masking policies exist).
        """
        role_name = role if isinstance(role, str) else role.value

        if role_name == _ADMIN_ROLE:
            return table

        acl = self.get_acl(dataset, role_name)

        result = table
        if acl is not None:
            result = self._filter_columns(result, acl)
            result = self._filter_rows(result, acl)

        # Apply Gravitino masking policies (v1.4.2)
        result = self._apply_masking(result, dataset, role_name)
        return result

    def _apply_masking(self, table: pa.Table, dataset: str, role: str) -> pa.Table:
        """Apply column-level masking from Gravitino policies if engine is available."""
        engine = getattr(self, "_masking_engine", None)
        if engine is None:
            return table
        try:
            return engine.apply_masking(table, dataset=dataset, role=role)
        except Exception:
            logger.warning("rbac.masking_failed", dataset=dataset, role=role, exc_info=True)
            return table

    def set_masking_engine(self, engine: Any) -> None:
        """Inject the Gravitino MaskingEngine (called during app startup)."""
        self._masking_engine = engine

    @staticmethod
    def _filter_columns(table: pa.Table, acl: DatasetACL) -> pa.Table:
        """Remove columns not in visible_columns whitelist."""
        if not acl.visible_columns:
            return table

        available = frozenset(table.column_names)
        keep = [c for c in table.column_names if c in (available & acl.visible_columns)]
        if not keep:
            return table.slice(0, 0)
        if len(keep) == len(table.column_names):
            return table
        return table.select(keep)

    @staticmethod
    def _filter_rows(table: pa.Table, acl: DatasetACL) -> pa.Table:
        """Apply row_filter expression to remove filtered rows."""
        if not acl.row_filter or table.num_rows == 0:
            return table

        return _apply_row_filter(table, acl.row_filter)


def _apply_row_filter(table: pa.Table, expr: str) -> pa.Table:
    """Apply a simple row filter expression to a PyArrow table.

    Supports simple comparisons: ``column op value``
    where op is one of: ==, !=, <, <=, >, >=

    For complex filters, use DuckDB SQL queries instead.
    """
    import re

    _OPS: dict[str, Any] = {
        "==": pc.equal, "!=": pc.not_equal,
        "<": pc.less, "<=": pc.less_equal,
        ">": pc.greater, ">=": pc.greater_equal,
    }

    pattern = re.compile(r'^(\w+)\s*(==|!=|<=|>=|<|>)\s*(.+)$')
    m = pattern.match(expr.strip())
    if not m:
        logger.warning("acl_filter_unparseable", expr=expr)
        return table

    col_name, op_str, raw_val = m.groups()
    if col_name not in table.column_names:
        logger.warning("acl_filter_column_missing", column=col_name)
        return table

    op = _OPS[op_str]
    col = table.column(col_name)

    val: Any = raw_val.strip().strip("'\"")
    try:
        val = float(val)
        if val == int(val):
            val = int(val)
    except ValueError:
        pass

    try:
        mask = op(col, val)
        return table.filter(mask)
    except (pa.ArrowInvalid, TypeError, pa.ArrowNotImplementedError):
        logger.warning("acl_filter_type_mismatch", column=col_name, value=val)
        return table


class GravitinoRBACBridge:
    """Delegate permission checks to Gravitino RBAC.

    Returns None when Gravitino is unavailable, signalling callers
    to fall back to local RBAC.

    Args:
        uri: Gravitino REST URI.
        metalake: Metalake name.
    """

    _ACTION_TO_PRIVILEGE: dict[str, str] = {
        "read": "SELECT_TABLE",
        "write": "INSERT_TABLE",
        "create": "CREATE_TABLE",
        "delete": "DELETE_TABLE",
        "admin": "CREATE_CATALOG",
    }

    def __init__(self, uri: str, metalake: str) -> None:
        self._uri = uri
        self._metalake = metalake
        self._client: Any = None
        self._initialized = False

    def _ensure_client(self) -> bool:
        if self._initialized:
            return self._client is not None
        self._initialized = True
        try:
            from gravitino.client.gravitino_client import GravitinoClient  # type: ignore[import-untyped]

            self._client = GravitinoClient(
                uri=self._uri, metalake_name=self._metalake
            )
            return True
        except Exception as exc:
            logger.warning("gravitino_rbac_client_init_failed", error=str(exc))
            self._client = None
            return False

    def check_permission(self, user: str, resource: str, action: str) -> bool | None:
        """Check if user has permission on resource for action.

        Returns:
            True if allowed, False if denied, None if Gravitino unavailable.
        """
        if not self._ensure_client():
            return None
        try:
            privilege_name = self._ACTION_TO_PRIVILEGE.get(action)
            if privilege_name is None:
                logger.warning("gravitino_rbac_unknown_action", action=action)
                return None
            metalake = self._client.load_metalake(self._metalake)
            if metalake is None:
                return None
            privs = metalake.supports_authorization().get_authorizations(user)
            if privs is None:
                return None
            return any(
                getattr(p, "name", lambda: str(p))() == privilege_name
                for p in privs
            )
        except Exception as exc:
            logger.warning(
                "gravitino_rbac_check_failed",
                user=user,
                resource=resource,
                action=action,
                error=str(exc),
            )
            return None
