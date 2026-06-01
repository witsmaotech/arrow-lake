"""Unit tests for v1.5.1 RBAC enhancements — Schema ACL + Deny-First."""

from __future__ import annotations

import pytest

from arrow_lake.api.rbac import DatasetACL, PermissionChecker, SchemaACL


class TestSchemaACLDataclass:
    """Test SchemaACL frozen dataclass."""

    def test_frozen(self) -> None:
        acl = SchemaACL(schema="ns", role="viewer", allowed_actions=frozenset({"read"}))
        with pytest.raises(AttributeError):
            acl.schema = "other"  # type: ignore[misc]

    def test_defaults(self) -> None:
        acl = SchemaACL(schema="ns", role="viewer")
        assert acl.allowed_actions == frozenset()
        assert acl.denied_actions == frozenset()


class TestDatasetACLDeniedActions:
    """Test DatasetACL.denied_actions field."""

    def test_default_empty(self) -> None:
        acl = DatasetACL(dataset="ds", role="viewer")
        assert acl.denied_actions == frozenset()

    def test_with_denied(self) -> None:
        acl = DatasetACL(
            dataset="ds", role="viewer",
            denied_actions=frozenset({"delete"}),
        )
        assert "delete" in acl.denied_actions


class TestSchemaACLManagement:
    """Test PermissionChecker schema-level ACL CRUD."""

    def test_set_and_get(self) -> None:
        checker = PermissionChecker()
        acl = SchemaACL(schema="finance", role="viewer", allowed_actions=frozenset({"read"}))
        checker.set_schema_acl(acl)

        result = checker.get_schema_acl("finance", "viewer")
        assert result is not None
        assert "read" in result.allowed_actions

    def test_get_nonexistent(self) -> None:
        checker = PermissionChecker()
        assert checker.get_schema_acl("unknown", "viewer") is None

    def test_list_schema_acls(self) -> None:
        checker = PermissionChecker()
        checker.set_schema_acl(SchemaACL(schema="ns", role="viewer", allowed_actions=frozenset({"read"})))
        checker.set_schema_acl(SchemaACL(schema="ns", role="editor", allowed_actions=frozenset({"read", "write"})))

        acls = checker.list_schema_acls("ns")
        assert len(acls) == 2

    def test_list_schema_acls_empty(self) -> None:
        checker = PermissionChecker()
        assert checker.list_schema_acls("unknown") == []

    def test_delete_schema_acl(self) -> None:
        checker = PermissionChecker()
        checker.set_schema_acl(SchemaACL(schema="ns", role="viewer"))
        assert checker.delete_schema_acl("ns", "viewer") is True
        assert checker.get_schema_acl("ns", "viewer") is None

    def test_delete_nonexistent(self) -> None:
        checker = PermissionChecker()
        assert checker.delete_schema_acl("ns", "viewer") is False


class TestDenyManagement:
    """Test PermissionChecker explicit deny management."""

    def test_deny_and_check(self) -> None:
        checker = PermissionChecker()
        checker.deny_action("secret_ds", "read")
        assert "read" in checker.list_denies("secret_ds")

    def test_remove_deny(self) -> None:
        checker = PermissionChecker()
        checker.deny_action("ds", "read")
        assert checker.remove_deny("ds", "read") is True
        assert checker.list_denies("ds") == set()

    def test_remove_nonexistent_deny(self) -> None:
        checker = PermissionChecker()
        assert checker.remove_deny("ds", "read") is False

    def test_list_denies_empty(self) -> None:
        checker = PermissionChecker()
        assert checker.list_denies("unknown_ds") == set()

    def test_deny_cleanup_on_last_removal(self) -> None:
        checker = PermissionChecker()
        checker.deny_action("ds", "read")
        checker.deny_action("ds", "write")
        checker.remove_deny("ds", "read")
        checker.remove_deny("ds", "write")
        # After removing all denies, internal dict should clean up
        assert checker.list_denies("ds") == set()


class TestDenyFirstEvaluationOrder:
    """Test the five-layer evaluation chain: deny → ACL deny → dataset ACL → schema ACL → role default."""

    def test_admin_bypasses_everything(self) -> None:
        checker = PermissionChecker()
        checker.deny_action("ds", "read")
        assert checker.check_dataset_access(role="admin", dataset="ds", action="read") is True

    def test_deny_list_overrides_schema_grant(self) -> None:
        """Explicit deny on a dataset overrides schema-level grant."""
        checker = PermissionChecker()
        checker.set_schema_acl(SchemaACL(
            schema="ns", role="viewer", allowed_actions=frozenset({"read"}),
        ))
        checker.deny_action("ns__secret", "read")

        assert checker.check_dataset_access(role="viewer", dataset="ns__secret", action="read") is False

    def test_dataset_acl_denied_actions_overrides_grant(self) -> None:
        """DatasetACL.denied_actions overrides granted actions."""
        checker = PermissionChecker()
        # Grant read+write via grant_dataset_access
        checker.grant_dataset_access("ds", "viewer", "read")
        checker.grant_dataset_access("ds", "viewer", "write")
        # Set ACL with denied_actions=write
        acl = DatasetACL(
            dataset="ds", role="viewer",
            denied_actions=frozenset({"write"}),
        )
        checker.set_acl(acl)

        assert checker.check_dataset_access(role="viewer", dataset="ds", action="read") is True
        assert checker.check_dataset_access(role="viewer", dataset="ds", action="write") is False

    def test_schema_acl_denied_actions_overrides_grant(self) -> None:
        """SchemaACL.denied_actions overrides allowed_actions."""
        checker = PermissionChecker()
        checker.set_schema_acl(SchemaACL(
            schema="ns", role="viewer",
            allowed_actions=frozenset({"read", "write"}),
            denied_actions=frozenset({"write"}),
        ))

        # Dataset inherits from schema
        assert checker.check_dataset_access(role="viewer", dataset="ns__ds", action="read") is True
        assert checker.check_dataset_access(role="viewer", dataset="ns__ds", action="write") is False

    def test_dataset_acl_takes_precedence_over_schema(self) -> None:
        """Per-dataset ACL takes precedence over schema inheritance."""
        checker = PermissionChecker()
        # Schema grants read only
        checker.set_schema_acl(SchemaACL(
            schema="ns", role="viewer", allowed_actions=frozenset({"read"}),
        ))
        # Dataset-level grants write via grant_dataset_access
        checker.grant_dataset_access("ns__special", "viewer", "write")

        assert checker.check_dataset_access(role="viewer", dataset="ns__special", action="write") is True

    def test_schema_inheritance_for_child_datasets(self) -> None:
        """Schema grant propagates to child datasets."""
        checker = PermissionChecker()
        checker.set_schema_acl(SchemaACL(
            schema="finance", role="editor",
            allowed_actions=frozenset({"read", "write"}),
        ))

        assert checker.check_dataset_access(role="editor", dataset="finance__revenue", action="read") is True
        assert checker.check_dataset_access(role="editor", dataset="finance__expenses", action="write") is True

    def test_fallback_to_role_default(self) -> None:
        """When no ACL or schema, falls back to role-based permission matrix."""
        checker = PermissionChecker()
        assert checker.check_dataset_access(role="viewer", dataset="unknown_ds", action="read") is True

    def test_grant_schema_read_deny_one_table_returns_403(self) -> None:
        """Scenario: grant schema READ, deny one table -> denied table is blocked."""
        checker = PermissionChecker()
        checker.set_schema_acl(SchemaACL(
            schema="analytics", role="viewer",
            allowed_actions=frozenset({"read"}),
        ))
        checker.deny_action("analytics__pii", "read")

        assert checker.check_dataset_access(role="viewer", dataset="analytics__metrics", action="read") is True
        assert checker.check_dataset_access(role="viewer", dataset="analytics__pii", action="read") is False

    def test_schema_editor_grant_allows_append(self) -> None:
        """Schema EDITOR grant allows append to any child dataset."""
        checker = PermissionChecker()
        checker.set_schema_acl(SchemaACL(
            schema="data", role="editor",
            allowed_actions=frozenset({"read", "write", "append"}),
        ))

        assert checker.check_dataset_access(role="editor", dataset="data__logs", action="append") is True
        assert checker.check_dataset_access(role="editor", dataset="data__events", action="read") is True


class TestInferSchema:
    """Test _infer_schema helper for namespace extraction."""

    def test_double_underscore_convention(self) -> None:
        assert PermissionChecker._infer_schema("finance__revenue") == "finance"

    def test_no_double_underscore(self) -> None:
        assert PermissionChecker._infer_schema("plain_name") is None

    def test_multiple_double_underscore(self) -> None:
        # Only splits on first __
        assert PermissionChecker._infer_schema("a__b__c") == "a"
