"""Role-Based Access Control (RBAC) permission system.

Defines role-permission matrix, dataset-level ACLs, and PermissionChecker
for enforcing access control in API endpoints.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arrow_lake.api.auth_models import Role


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

# Admin always overrides ACL checks
_ADMIN_ROLE = "admin"


class PermissionChecker:
    """Evaluate role-permission matrix and dataset-level ACLs.

    In-memory ACL storage — sufficient for single-team deployments.
    Interface designed for trivial upgrade to database-backed store.
    """

    def __init__(self) -> None:
        # dataset -> role -> set of granted actions
        self._dataset_acls: dict[str, dict[str, set[str]]] = {}

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

        # ADMIN always has full access
        if role_name == _ADMIN_ROLE:
            return True

        # Check dataset-specific ACL grants first (override global perms)
        dataset_acl = self._dataset_acls.get(dataset, {})
        role_grants = dataset_acl.get(role_name)
        if role_grants is not None:
            return action in role_grants

        # No specific ACL — use global role permissions
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
