"""Role-Based Access Control (RBAC) permission system.

Defines role-permission matrix, dataset-level ACLs, row/column-level ACLs,
and PermissionChecker for enforcing access control in API endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

import pyarrow as pa
import pyarrow.compute as pc
import structlog

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

_ADMIN_ROLE = Role.ADMIN.value


class AclStoreUnavailable(RuntimeError):
    """The RBAC control-plane store raised while reading row/column ACLs.

    v1.10.7 review B-2: returning ``None`` (no restriction) on a store failure
    meant every WP1 enforcement point silently ran unfiltered — a control-plane
    outage must refuse the data plane instead (fail-closed → 503 via the app
    exception handler), never pretend there is no ACL.
    """


@dataclass(frozen=True)
class DatasetACL:
    """Row/column-level access control for a dataset.

    Attributes:
        dataset: Dataset name.
        role: Role this ACL applies to.
        visible_columns: Columns this role can see (empty = all).
        row_filter: SQL-style WHERE expression for row filtering (empty = all rows).
        denied_actions: Actions explicitly denied (overrides inherited grants).
    """

    dataset: str
    role: str
    visible_columns: frozenset[str] = frozenset()
    row_filter: str = ""
    denied_actions: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SchemaACL:
    """Schema-level ACL inherited by all child datasets.

    Attributes:
        schema: Schema (namespace) name.
        role: Role this ACL applies to.
        allowed_actions: Actions granted at schema level.
        denied_actions: Actions explicitly denied (overrides allowed_actions).
    """

    schema: str
    role: str
    allowed_actions: frozenset[str] = frozenset()
    denied_actions: frozenset[str] = frozenset()


class PermissionChecker:
    """Evaluate role-permission matrix and dataset-level ACLs.

    In-memory ACL storage — sufficient for single-team deployments.
    Interface designed for trivial upgrade to database-backed store.
    """

    def __init__(self, rbac_store: Any = None) -> None:
        # v1.9.0: optional RbacStore (libSQL) — when present it is the source of
        # truth for the four ACL dicts below (which then only serve as the
        # in-memory fallback for deployments with system_db disabled).
        self._store = rbac_store
        # dataset -> role -> set of granted actions
        self._dataset_acls: dict[str, dict[str, set[str]]] = {}
        # dataset -> role -> DatasetACL (row/column level)
        self._row_col_acls: dict[str, dict[str, DatasetACL]] = {}
        # schema -> role -> SchemaACL (inherited by child datasets)
        self._schema_acls: dict[str, dict[str, SchemaACL]] = {}
        # dataset -> set of globally denied actions (any role)
        self._deny_list: dict[str, set[str]] = {}

    def set_system_store(self, store: Any) -> None:
        """Inject the RbacStore (libSQL). Called from app lifespan when system_db is enabled."""
        self._store = store

    # ------------------------------------------------------------------
    # Store-backed read helpers.
    #
    # Fail-close semantics: when the store is present but a read raises —
    # * role/grant/schema reads log and return an empty result, which
    #   propagates as deny (``has_permission`` → False, no grants →
    #   role-default with empty perms);
    # * row/col ACL reads raise ``AclStoreUnavailable`` (B-2) — returning
    #   ``None`` there would read as "no restriction" at every enforcement
    #   point, so an unreachable control-plane DB must surface as 503
    #   instead of silently allowing unfiltered reads.
    # ------------------------------------------------------------------
    def _role_perms(self, role_name: str) -> frozenset[str]:
        if self._store is not None:
            try:
                return self._store.get_role_permissions(role_name)
            except Exception:
                logger.warning("rbac.store_role_read_failed", role=role_name, exc_info=True)
                return frozenset()
        return _ROLE_PERMISSIONS.get(role_name, frozenset())

    def _get_dataset_grant(
        self, dataset: str, role_name: str
    ) -> set[str] | None:
        if self._store is not None:
            try:
                return self._store.get_dataset_grants(dataset).get(role_name)
            except Exception:
                logger.warning("rbac.store_grant_read_failed", dataset=dataset, exc_info=True)
                return None
        return self._dataset_acls.get(dataset, {}).get(role_name)

    def _get_row_col_acl(
        self, dataset: str, role_name: str
    ) -> DatasetACL | None:
        """Layered lookup (DR14 W3.4/D4): dataset default + table override.

        A two-part key (``ds.table`` — a container table) resolves the
        table-level override first (store key ``ds::table``; dataset names
        cannot contain ':' so the convention is collision-free), then falls
        back to the container dataset's ACL. Plain names unchanged.
        """
        if "." in dataset:
            ds, _, table = dataset.partition(".")
            # Table-level override: store convention key first ("ds::table"),
            # then the dotted form (in-memory set_acl keys by acl.dataset
            # verbatim). Either hit replaces the container default.
            override = self._lookup_row_col(f"{ds}::{table}", role_name)
            if override is None:
                override = self._lookup_row_col(f"{ds}.{table}", role_name)
            if override is not None:
                return override
            return self._lookup_row_col(ds, role_name)
        return self._lookup_row_col(dataset, role_name)

    def _lookup_row_col(
        self, dataset: str, role_name: str
    ) -> DatasetACL | None:
        if self._store is not None:
            try:
                d = self._store.get_row_col_acl(dataset, role_name)
            except Exception as exc:
                # B-2: fail-closed. ``None`` means "no restriction" to every
                # caller (check_dataset_access deny step, rbac_sql rewrite) —
                # a store outage must not read as "no ACL configured".
                logger.warning("rbac.store_rowcol_read_failed", dataset=dataset, exc_info=True)
                raise AclStoreUnavailable(
                    f"Control-plane store unreachable while reading row/col ACL for '{dataset}'"
                ) from exc
            if d is None:
                return None
            return DatasetACL(
                dataset=d["dataset"], role=d["role"],
                visible_columns=d["visible_columns"],
                row_filter=d["row_filter"],
                denied_actions=d["denied_actions"],
            )
        # In-memory path: exact hit first, then case-insensitive fallback —
        # DuckDB resolves identifiers case-insensitively, so a lookup from
        # the SQL enforcement layer may arrive in any case spelling (v1.10.7
        # review: exact-only matching let mixed-case dataset names escape
        # row/column ACL entirely).
        exact = self._row_col_acls.get(dataset, {}).get(role_name)
        if exact is not None:
            return exact
        lowered = dataset.lower()
        for name, roles in self._row_col_acls.items():
            if name.lower() == lowered and role_name in roles:
                return roles[role_name]
        return None

    def _get_schema_acl(
        self, schema: str, role_name: str
    ) -> SchemaACL | None:
        if self._store is not None:
            try:
                d = self._store.get_schema_acl(schema, role_name)
            except Exception:
                logger.warning("rbac.store_schema_read_failed", schema=schema, exc_info=True)
                return None
            if d is None:
                return None
            return SchemaACL(
                schema=d["schema"], role=d["role"],
                allowed_actions=d["allowed_actions"],
                denied_actions=d["denied_actions"],
            )
        return self._schema_acls.get(schema, {}).get(role_name)

    def _get_denies(self, dataset: str) -> set[str]:
        if self._store is not None:
            try:
                return self._store.list_denies(dataset)
            except Exception:
                logger.warning("rbac.store_deny_read_failed", dataset=dataset, exc_info=True)
                return set()
        return self._deny_list.get(dataset, set())

    def has_permission(self, role: str | Role, perm: str) -> bool:
        """Check if a role has a specific permission."""
        role_name = role if isinstance(role, str) else role.value
        return perm in self._role_perms(role_name)

    def get_permissions(self, role: str | Role) -> frozenset[str]:
        """Return all permissions for a role."""
        role_name = role if isinstance(role, str) else role.value
        return self._role_perms(role_name)

    def check_dataset_access(
        self,
        *,
        role: str | Role,
        dataset: str,
        action: str,
        permissions: list[str] | frozenset[str] | None = None,
    ) -> bool:
        """Check if a role can perform an action on a dataset.

        Evaluation order (Deny-first):
        1. Admin bypass
        2. Explicit Deny (per-dataset or per-schema)
        3. Per-dataset ACL grant
        4. Schema-level ACL inheritance
        5. Default: the token's permissions claim when non-empty
           (require_permission semantics, v1.10.5 M4 — scopes can restrict
           below the role or grant one action above it), else the role matrix.

        ``permissions`` only feeds step 5: deny and dataset/schema ACL layers
        (2-4) always evaluate from the role, so a write-scoped token can never
        bypass an explicit deny (v1.10.7 review B-4 follow-up).
        """
        role_name = role if isinstance(role, str) else role.value

        if role_name == _ADMIN_ROLE:
            return True

        # 2. Explicit Deny — per-dataset deny list
        if action in self._get_denies(dataset):
            return False

        # 2b. Explicit Deny — DatasetACL.denied_actions
        rc_acl = self._get_row_col_acl(dataset, role_name)
        if rc_acl is not None and action in rc_acl.denied_actions:
            return False

        # 2c. Explicit Deny — SchemaACL.denied_actions
        schema = self._infer_schema(dataset)
        if schema:
            schema_acl = self._get_schema_acl(schema, role_name)
            if schema_acl and action in schema_acl.denied_actions:
                return False

        # 3. Per-dataset ACL grant
        role_grants = self._get_dataset_grant(dataset, role_name)
        if role_grants is not None:
            return action in role_grants

        # 4. Schema-level ACL inheritance
        if schema:
            schema_acl = self._get_schema_acl(schema, role_name)
            if schema_acl and action in schema_acl.allowed_actions:
                return True

        # 5. Default — token permissions claim (non-empty) else role matrix
        full_perm = f"dataset:{action}"
        if permissions:
            return full_perm in permissions
        return self.has_permission(role_name, full_perm)

    def grant_dataset_access(
        self, dataset: str, role: str | Role, action: str
    ) -> None:
        """Grant a role a specific action on a dataset."""
        role_name = role if isinstance(role, str) else role.value
        if self._store is not None:
            self._store.grant_dataset_access(dataset, role_name, action)
            return
        if dataset not in self._dataset_acls:
            self._dataset_acls[dataset] = {}
        if role_name not in self._dataset_acls[dataset]:
            self._dataset_acls[dataset][role_name] = set()
        self._dataset_acls[dataset][role_name].add(action)

    def revoke_dataset_access(self, dataset: str, role: str | Role) -> None:
        """Revoke all actions for a role on a dataset."""
        role_name = role if isinstance(role, str) else role.value
        if self._store is not None:
            self._store.revoke_dataset_access(dataset, role_name)
            return
        if dataset in self._dataset_acls and role_name in self._dataset_acls[dataset]:
            del self._dataset_acls[dataset][role_name]

    # ------------------------------------------------------------------
    # Row/column ACL management
    # ------------------------------------------------------------------

    def set_acl(self, acl: DatasetACL) -> None:
        """Set row/column ACL for a dataset + role."""
        if self._store is not None:
            self._store.set_row_col_acl(
                acl.dataset, acl.role,
                visible_columns=sorted(acl.visible_columns),
                row_filter=acl.row_filter,
                denied_actions=sorted(acl.denied_actions),
            )
        else:
            if acl.dataset not in self._row_col_acls:
                self._row_col_acls[acl.dataset] = {}
            self._row_col_acls[acl.dataset][acl.role] = acl
        logger.info("acl_set", dataset=acl.dataset, role=acl.role)

    def get_acl(self, dataset: str, role: str | Role) -> DatasetACL | None:
        """Get row/column ACL for a dataset + role."""
        role_name = role if isinstance(role, str) else role.value
        return self._get_row_col_acl(dataset, role_name)

    def list_acls(self, dataset: str) -> list[DatasetACL]:
        """List all row/column ACLs for a dataset."""
        if self._store is not None:
            try:
                rows = self._store.list_row_col_acls(dataset)
            except Exception:
                logger.warning("rbac.store_list_rowcol_failed", dataset=dataset, exc_info=True)
                return []
            return [
                DatasetACL(
                    dataset=r["dataset"], role=r["role"],
                    visible_columns=r["visible_columns"],
                    row_filter=r["row_filter"],
                    denied_actions=r["denied_actions"],
                )
                for r in rows
            ]
        return list(self._row_col_acls.get(dataset, {}).values())

    def delete_acl(self, dataset: str, role: str | Role) -> bool:
        """Delete row/column ACL for a dataset + role. Returns True if found."""
        role_name = role if isinstance(role, str) else role.value
        if self._store is not None:
            return self._store.delete_row_col_acl(dataset, role_name)
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
        """Apply column-level masking from Gravitino policies if engine is available.

        Fail-closed: on masking failure, return an EMPTY table (same schema,
        zero rows) rather than the unmasked original — never leak sensitive
        columns when the masking engine is configured but errors mid-apply.
        Callers see an empty result (no rows) instead of unmasked data."""
        engine = getattr(self, "_masking_engine", None)
        if engine is None:
            return table
        try:
            return engine.apply_masking(table, dataset=dataset, role=role)
        except Exception:
            logger.error(
                "rbac.masking_failed_fail_closed",
                dataset=dataset, role=role, exc_info=True,
                msg="masking failed — returning empty table (fail-closed, no unmasked data)",
            )
            return table.slice(0, 0)

    def set_masking_engine(self, engine: Any) -> None:
        """Inject the Gravitino MaskingEngine (called during app startup)."""
        self._masking_engine = engine

    # ------------------------------------------------------------------
    # Schema-level ACL management
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_schema(dataset: str) -> str | None:
        """Infer schema from dataset name. Convention: namespace__dataset → namespace."""
        if "__" in dataset:
            return dataset.split("__", 1)[0]
        return None

    def set_schema_acl(self, acl: SchemaACL) -> None:
        """Set schema-level ACL inherited by all child datasets."""
        if self._store is not None:
            self._store.set_schema_acl(
                acl.schema, acl.role,
                allowed_actions=sorted(acl.allowed_actions),
                denied_actions=sorted(acl.denied_actions),
            )
        else:
            if acl.schema not in self._schema_acls:
                self._schema_acls[acl.schema] = {}
            self._schema_acls[acl.schema][acl.role] = acl
        logger.info("schema_acl_set", schema=acl.schema, role=acl.role)

    def get_schema_acl(self, schema: str, role: str | Role) -> SchemaACL | None:
        """Get schema-level ACL for a schema + role."""
        role_name = role if isinstance(role, str) else role.value
        return self._get_schema_acl(schema, role_name)

    def delete_schema_acl(self, schema: str, role: str | Role) -> bool:
        """Delete schema-level ACL. Returns True if found."""
        role_name = role if isinstance(role, str) else role.value
        if self._store is not None:
            return self._store.delete_schema_acl(schema, role_name)
        acls = self._schema_acls.get(schema)
        if acls and role_name in acls:
            del acls[role_name]
            logger.info("schema_acl_deleted", schema=schema, role=role_name)
            return True
        return False

    def list_schema_acls(self, schema: str) -> list[SchemaACL]:
        """List all schema-level ACLs for a schema."""
        if self._store is not None:
            try:
                rows = self._store.list_schema_acls(schema)
            except Exception:
                logger.warning("rbac.store_list_schema_failed", schema=schema, exc_info=True)
                return []
            return [
                SchemaACL(
                    schema=r["schema"], role=r["role"],
                    allowed_actions=r["allowed_actions"],
                    denied_actions=r["denied_actions"],
                )
                for r in rows
            ]
        return list(self._schema_acls.get(schema, {}).values())

    # ------------------------------------------------------------------
    # Explicit Deny management
    # ------------------------------------------------------------------

    def deny_action(self, dataset: str, action: str) -> None:
        """Add an explicit Deny for an action on a dataset."""
        if self._store is not None:
            self._store.deny_action(dataset, action)
            logger.info("deny_added", dataset=dataset, action=action)
            return
        if dataset not in self._deny_list:
            self._deny_list[dataset] = set()
        self._deny_list[dataset].add(action)
        logger.info("deny_added", dataset=dataset, action=action)

    def remove_deny(self, dataset: str, action: str) -> bool:
        """Remove an explicit Deny. Returns True if found."""
        if self._store is not None:
            return self._store.remove_deny(dataset, action)
        denies = self._deny_list.get(dataset)
        if denies and action in denies:
            denies.discard(action)
            if not denies:
                del self._deny_list[dataset]
            logger.info("deny_removed", dataset=dataset, action=action)
            return True
        return False

    def list_denies(self, dataset: str) -> set[str]:
        """List all denied actions for a dataset."""
        return set(self._get_denies(dataset))

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
        return table.slice(0, 0)  # fail-closed (review H3)

    col_name, op_str, raw_val = m.groups()
    if col_name not in table.column_names:
        logger.warning("acl_filter_column_missing", column=col_name)
        return table.slice(0, 0)  # fail-closed (review H3)

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
        return table.slice(0, 0)  # fail-closed (review H3)


class GravitinoRBACBridge:
    """Delegate permission checks to Gravitino RBAC.

    Returns None when Gravitino is unavailable, signalling callers
    to fall back to local RBAC.

    Args:
        uri: Gravitino REST URI.
        metalake: Metalake name.
    """

    _ACTION_TO_PRIVILEGE: ClassVar[dict[str, str]] = {
        "read": "SELECT_TABLE",
        "write": "MODIFY_TABLE",
        "create": "CREATE_TABLE",
        "delete": "DROP_TABLE",
        "admin": "ALL",
        "append": "INSERT_TABLE",
        "update": "UPDATE_TABLE",
        "query": "SELECT_TABLE",
        "export": "SELECT_TABLE",
        "ingest": "INSERT_TABLE",
        "schema_create": "CREATE_TABLE",
        "schema_list": "USAGE",
        "tag_manage": "USE_CATALOG",
        "policy_manage": "USE_CATALOG",
        "stats_collect": "USAGE",
    }

    def __init__(self, uri: str, metalake: str, auth_provider: Any = None) -> None:
        self._uri = uri
        self._metalake = metalake
        self._auth_provider = auth_provider
        self._client: Any = None
        self._initialized = False

    def _ensure_client(self) -> bool:
        if self._initialized:
            return self._client is not None
        self._initialized = True
        try:
            from gravitino.client.gravitino_client import (
                GravitinoClient,  # type: ignore[import-untyped]
            )

            kwargs: dict[str, Any] = {"uri": self._uri, "metalake_name": self._metalake}
            if self._auth_provider is not None:
                headers = self._auth_provider.auth_headers()
                if headers:
                    kwargs["request_headers"] = headers
            self._client = GravitinoClient(**kwargs)
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
                getattr(p, "name", lambda p=p: str(p))() == privilege_name
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
