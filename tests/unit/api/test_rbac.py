"""Tests for api/rbac.py — PermissionChecker, ACL, deny-first, row/column filtering."""

from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow as pa
import pytest

from arrow_lake.api.auth_models import Role
from arrow_lake.api.rbac import (
    DatasetACL,
    Permission,
    PermissionChecker,
    SchemaACL,
    _apply_row_filter,
)


def _table() -> pa.Table:
    return pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"], "age": [20, 30, 40]})


# ===========================================================================
# Permission enum
# ===========================================================================


class TestPermission:
    def test_permission_values(self) -> None:
        assert Permission.DATASET_READ == "dataset:read"
        assert Permission.DATASET_WRITE == "dataset:write"
        assert Permission.DATASET_DELETE == "dataset:delete"
        assert Permission.ADMIN_MANAGE == "admin:manage"


# ===========================================================================
# has_permission / get_permissions
# ===========================================================================


class TestHasPermission:
    def test_viewer_can_read(self) -> None:
        assert PermissionChecker().has_permission("viewer", Permission.DATASET_READ)

    def test_viewer_cannot_write(self) -> None:
        assert not PermissionChecker().has_permission("viewer", Permission.DATASET_WRITE)

    def test_editor_can_write(self) -> None:
        assert PermissionChecker().has_permission("editor", Permission.DATASET_WRITE)

    def test_admin_has_all(self) -> None:
        pc = PermissionChecker()
        assert pc.has_permission(Role.ADMIN, Permission.ADMIN_MANAGE)
        assert pc.has_permission(Role.ADMIN, Permission.DATASET_DELETE)

    def test_role_enum_works(self) -> None:
        assert PermissionChecker().has_permission(Role.VIEWER, Permission.DATASET_READ)

    def test_unknown_role_has_no_permissions(self) -> None:
        assert not PermissionChecker().has_permission("guest", Permission.DATASET_READ)


class TestGetPermissions:
    def test_viewer_permissions(self) -> None:
        perms = PermissionChecker().get_permissions("viewer")
        assert Permission.DATASET_READ in perms
        assert len(perms) == 1

    def test_admin_has_all_permissions(self) -> None:
        perms = PermissionChecker().get_permissions("admin")
        assert len(perms) == len(Permission)


# ===========================================================================
# check_dataset_access — deny-first evaluation
# ===========================================================================


class TestCheckDatasetAccess:
    def test_admin_bypass(self) -> None:
        assert PermissionChecker().check_dataset_access(
            role="admin", dataset="ds", action="read"
        )

    def test_default_role_permission(self) -> None:
        assert PermissionChecker().check_dataset_access(
            role="viewer", dataset="ds", action="read"
        )

    def test_default_role_denied(self) -> None:
        assert not PermissionChecker().check_dataset_access(
            role="viewer", dataset="ds", action="write"
        )

    def test_explicit_deny_overrides_role(self) -> None:
        pc = PermissionChecker()
        pc.deny_action("ds", "read")
        assert not pc.check_dataset_access(role="viewer", dataset="ds", action="read")

    def test_deny_list_admin_bypasses(self) -> None:
        pc = PermissionChecker()
        pc.deny_action("ds", "read")
        assert pc.check_dataset_access(role="admin", dataset="ds", action="read")

    def test_grant_dataset_access(self) -> None:
        pc = PermissionChecker()
        pc.grant_dataset_access("ds", "viewer", "write")
        assert pc.check_dataset_access(role="viewer", dataset="ds", action="write")

    def test_revoke_dataset_access(self) -> None:
        pc = PermissionChecker()
        pc.grant_dataset_access("ds", "viewer", "write")
        pc.revoke_dataset_access("ds", "viewer")
        assert not pc.check_dataset_access(role="viewer", dataset="ds", action="write")

    def test_deny_in_dataset_acl(self) -> None:
        pc = PermissionChecker()
        acl = DatasetACL(dataset="ds", role="viewer", denied_actions=frozenset({"read"}))
        pc.set_acl(acl)
        assert not pc.check_dataset_access(role="viewer", dataset="ds", action="read")

    def test_schema_acl_inheritance(self) -> None:
        pc = PermissionChecker()
        sacl = SchemaACL(schema="ns", role="viewer", allowed_actions=frozenset({"write"}))
        pc.set_schema_acl(sacl)
        assert pc.check_dataset_access(role="viewer", dataset="ns__ds", action="write")

    def test_schema_deny_overrides_allow(self) -> None:
        pc = PermissionChecker()
        sacl = SchemaACL(
            schema="ns", role="viewer",
            allowed_actions=frozenset({"write"}),
            denied_actions=frozenset({"write"}),
        )
        pc.set_schema_acl(sacl)
        assert not pc.check_dataset_access(role="viewer", dataset="ns__ds", action="write")


# ===========================================================================
# ACL CRUD
# ===========================================================================


class TestACLCrud:
    def test_set_and_get_acl(self) -> None:
        pc = PermissionChecker()
        acl = DatasetACL(dataset="ds", role="viewer", visible_columns=frozenset({"id"}))
        pc.set_acl(acl)
        result = pc.get_acl("ds", "viewer")
        assert result is not None
        assert "id" in result.visible_columns

    def test_list_acls(self) -> None:
        pc = PermissionChecker()
        pc.set_acl(DatasetACL(dataset="ds", role="viewer"))
        pc.set_acl(DatasetACL(dataset="ds", role="editor"))
        assert len(pc.list_acls("ds")) == 2

    def test_delete_acl(self) -> None:
        pc = PermissionChecker()
        pc.set_acl(DatasetACL(dataset="ds", role="viewer"))
        assert pc.delete_acl("ds", "viewer") is True
        assert pc.delete_acl("ds", "viewer") is False

    def test_get_acl_missing(self) -> None:
        assert PermissionChecker().get_acl("ds", "viewer") is None

    def test_list_acls_empty(self) -> None:
        assert PermissionChecker().list_acls("ds") == []


# ===========================================================================
# Schema ACL CRUD
# ===========================================================================


class TestSchemaACLCrud:
    def test_set_and_get(self) -> None:
        pc = PermissionChecker()
        sacl = SchemaACL(schema="ns", role="editor", allowed_actions=frozenset({"write"}))
        pc.set_schema_acl(sacl)
        result = pc.get_schema_acl("ns", "editor")
        assert result is not None
        assert "write" in result.allowed_actions

    def test_delete(self) -> None:
        pc = PermissionChecker()
        pc.set_schema_acl(SchemaACL(schema="ns", role="editor"))
        assert pc.delete_schema_acl("ns", "editor") is True
        assert pc.delete_schema_acl("ns", "editor") is False

    def test_list_schema_acls(self) -> None:
        pc = PermissionChecker()
        pc.set_schema_acl(SchemaACL(schema="ns", role="viewer"))
        pc.set_schema_acl(SchemaACL(schema="ns", role="editor"))
        assert len(pc.list_schema_acls("ns")) == 2


# ===========================================================================
# Deny list management
# ===========================================================================


class TestDenyList:
    def test_deny_and_list(self) -> None:
        pc = PermissionChecker()
        pc.deny_action("ds", "read")
        assert "read" in pc.list_denies("ds")

    def test_remove_deny(self) -> None:
        pc = PermissionChecker()
        pc.deny_action("ds", "read")
        assert pc.remove_deny("ds", "read") is True
        assert pc.remove_deny("ds", "read") is False

    def test_list_denies_empty(self) -> None:
        assert PermissionChecker().list_denies("ds") == set()


# ===========================================================================
# Table filtering — columns
# ===========================================================================


class TestFilterColumns:
    def test_filter_visible_columns(self) -> None:
        pc = PermissionChecker()
        acl = DatasetACL(dataset="ds", role="viewer", visible_columns=frozenset({"id", "name"}))
        pc.set_acl(acl)
        result = pc.apply_table_filter(_table(), dataset="ds", role="viewer")
        assert "age" not in result.column_names
        assert "id" in result.column_names

    def test_no_filter_when_all_visible(self) -> None:
        pc = PermissionChecker()
        result = pc.apply_table_filter(_table(), dataset="ds", role="viewer")
        assert result.num_columns == 3

    def test_admin_no_filter(self) -> None:
        pc = PermissionChecker()
        acl = DatasetACL(dataset="ds", role="admin", visible_columns=frozenset({"id"}))
        pc.set_acl(acl)
        result = pc.apply_table_filter(_table(), dataset="ds", role="admin")
        assert result.num_columns == 3


# ===========================================================================
# Row filtering
# ===========================================================================


class TestRowFilter:
    def test_filter_equals(self) -> None:
        table = _table()
        result = _apply_row_filter(table, "age == 30")
        assert result.num_rows == 1
        assert result.column("name")[0].as_py() == "b"

    def test_filter_greater(self) -> None:
        result = _apply_row_filter(_table(), "age > 25")
        assert result.num_rows == 2

    def test_filter_unparseable_returns_all(self) -> None:
        result = _apply_row_filter(_table(), "not a valid filter")
        assert result.num_rows == 3

    def test_filter_missing_column_returns_all(self) -> None:
        result = _apply_row_filter(_table(), "missing_col == 1")
        assert result.num_rows == 3


# ===========================================================================
# _infer_schema
# ===========================================================================


class TestInferSchema:
    def test_double_underscore(self) -> None:
        assert PermissionChecker._infer_schema("ns__ds") == "ns"

    def test_no_underscore(self) -> None:
        assert PermissionChecker._infer_schema("ds") is None

    def test_multiple_underscores(self) -> None:
        assert PermissionChecker._infer_schema("ns__ds__extra") == "ns"


# ===========================================================================
# Masking engine
# ===========================================================================


class TestMaskingEngine:
    def test_masking_applied(self) -> None:
        pc = PermissionChecker()
        mock_engine = MagicMock()
        mock_engine.apply_masking.return_value = _table()
        pc.set_masking_engine(mock_engine)
        pc.apply_table_filter(_table(), dataset="ds", role="viewer")
        mock_engine.apply_masking.assert_called_once()

    def test_masking_not_called_for_admin(self) -> None:
        pc = PermissionChecker()
        mock_engine = MagicMock()
        pc.set_masking_engine(mock_engine)
        pc.apply_table_filter(_table(), dataset="ds", role="admin")
        mock_engine.apply_masking.assert_not_called()
