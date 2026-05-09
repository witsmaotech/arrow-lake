"""Unit tests for RBAC permission system."""

from __future__ import annotations

import pytest
from arrow_lake.api.auth_models import Role
from arrow_lake.api.rbac import Permission, PermissionChecker

# ---------------------------------------------------------------------------
# Permission enum
# ---------------------------------------------------------------------------


def test_permission_values() -> None:
    assert Permission.DATASET_READ == "dataset:read"
    assert Permission.DATASET_WRITE == "dataset:write"
    assert Permission.DATASET_DELETE == "dataset:delete"
    assert Permission.ADMIN_MANAGE == "admin:manage"
    assert len(Permission) == 4


# ---------------------------------------------------------------------------
# PermissionChecker — role hierarchy
# ---------------------------------------------------------------------------


@pytest.fixture
def checker() -> PermissionChecker:
    return PermissionChecker()


def test_admin_has_all_permissions(checker: PermissionChecker) -> None:
    """ADMIN should have all permissions."""
    for perm in Permission:
        assert checker.has_permission(Role.ADMIN, perm)


def test_editor_dataset_permissions(checker: PermissionChecker) -> None:
    """EDITOR should have read + write but not admin."""
    assert checker.has_permission(Role.EDITOR, Permission.DATASET_READ)
    assert checker.has_permission(Role.EDITOR, Permission.DATASET_WRITE)
    assert not checker.has_permission(Role.EDITOR, Permission.ADMIN_MANAGE)


def test_viewer_read_only(checker: PermissionChecker) -> None:
    """VIEWER should only have read."""
    assert checker.has_permission(Role.VIEWER, Permission.DATASET_READ)
    assert not checker.has_permission(Role.VIEWER, Permission.DATASET_WRITE)
    assert not checker.has_permission(Role.VIEWER, Permission.DATASET_DELETE)
    assert not checker.has_permission(Role.VIEWER, Permission.ADMIN_MANAGE)


def test_check_permission_list(checker: PermissionChecker) -> None:
    """check_permissions returns all permissions for a role."""
    admin_perms = checker.get_permissions(Role.ADMIN)
    assert len(admin_perms) == len(Permission)

    viewer_perms = checker.get_permissions(Role.VIEWER)
    assert Permission.DATASET_READ in viewer_perms
    assert Permission.ADMIN_MANAGE not in viewer_perms


# ---------------------------------------------------------------------------
# Dataset ACL
# ---------------------------------------------------------------------------


def test_dataset_acl_allow(checker: PermissionChecker) -> None:
    """ADMIN can access any dataset."""
    assert checker.check_dataset_access(
        role=Role.ADMIN, dataset="my-data", action="write"
    )


def test_dataset_acl_editor_any_dataset(checker: PermissionChecker) -> None:
    """EDITOR can write to any dataset (no per-dataset ACL by default)."""
    assert checker.check_dataset_access(
        role=Role.EDITOR, dataset="my-data", action="write"
    )


def test_dataset_acl_viewer_read_only(checker: PermissionChecker) -> None:
    """VIEWER can read but not write."""
    assert checker.check_dataset_access(
        role=Role.VIEWER, dataset="my-data", action="read"
    )
    assert not checker.check_dataset_access(
        role=Role.VIEWER, dataset="my-data", action="write"
    )


def test_dataset_acl_grant_editor(checker: PermissionChecker) -> None:
    """Grant EDITOR access to a specific dataset for VIEWER."""
    checker.grant_dataset_access("sensitive-data", Role.VIEWER, "write")
    assert checker.check_dataset_access(
        role=Role.VIEWER, dataset="sensitive-data", action="write"
    )
    # Other datasets still read-only
    assert not checker.check_dataset_access(
        role=Role.VIEWER, dataset="other-data", action="write"
    )


def test_dataset_acl_revoke(checker: PermissionChecker) -> None:
    """Revoke dataset access falls back to global perms."""
    # Grant VIEWER write on a dataset (they normally can't)
    checker.grant_dataset_access("secret", Role.VIEWER, "write")
    assert checker.check_dataset_access(
        role=Role.VIEWER, dataset="secret", action="write"
    )
    # Revoke — falls back to global perms (VIEWER can't write)
    checker.revoke_dataset_access("secret", Role.VIEWER)
    assert not checker.check_dataset_access(
        role=Role.VIEWER, dataset="secret", action="write"
    )


def test_admin_override_dataset_acl(checker: PermissionChecker) -> None:
    """ADMIN always has full access regardless of ACL."""
    # Even if ACL explicitly denies
    assert checker.check_dataset_access(
        role=Role.ADMIN, dataset="anything", action="delete"
    )
