"""Tests for row/column ACL in PermissionChecker."""

from __future__ import annotations

import pyarrow as pa
import pytest

from arrow_lake.api.rbac import DatasetACL, PermissionChecker, _apply_row_filter


def _make_table(**columns: list) -> pa.Table:
    return pa.table(columns)


class TestDatasetACL:
    def test_frozen(self) -> None:
        acl = DatasetACL(dataset="ds", role="viewer")
        with pytest.raises(AttributeError):
            acl.dataset = "other"  # type: ignore[misc]

    def test_defaults(self) -> None:
        acl = DatasetACL(dataset="ds", role="viewer")
        assert acl.visible_columns == frozenset()
        assert acl.row_filter == ""


class TestAclStore:
    def test_set_and_get(self) -> None:
        checker = PermissionChecker()
        acl = DatasetACL(dataset="ds", role="viewer", visible_columns=frozenset({"a", "b"}))
        checker.set_acl(acl)
        result = checker.get_acl("ds", "viewer")
        assert result is not None
        assert result.visible_columns == frozenset({"a", "b"})

    def test_get_nonexistent(self) -> None:
        checker = PermissionChecker()
        assert checker.get_acl("ds", "viewer") is None

    def test_list_acls(self) -> None:
        checker = PermissionChecker()
        checker.set_acl(DatasetACL(dataset="ds", role="viewer", visible_columns=frozenset({"a"})))
        checker.set_acl(DatasetACL(dataset="ds", role="editor", row_filter="x > 5"))
        acls = checker.list_acls("ds")
        assert len(acls) == 2

    def test_list_acls_empty(self) -> None:
        checker = PermissionChecker()
        assert checker.list_acls("ds") == []

    def test_delete_acl(self) -> None:
        checker = PermissionChecker()
        checker.set_acl(DatasetACL(dataset="ds", role="viewer"))
        assert checker.delete_acl("ds", "viewer") is True
        assert checker.get_acl("ds", "viewer") is None

    def test_delete_nonexistent(self) -> None:
        checker = PermissionChecker()
        assert checker.delete_acl("ds", "viewer") is False

    def test_set_overwrites(self) -> None:
        checker = PermissionChecker()
        checker.set_acl(DatasetACL(dataset="ds", role="viewer", visible_columns=frozenset({"a"})))
        checker.set_acl(DatasetACL(dataset="ds", role="viewer", visible_columns=frozenset({"b", "c"})))
        result = checker.get_acl("ds", "viewer")
        assert result is not None
        assert result.visible_columns == frozenset({"b", "c"})


class TestColumnFilter:
    def test_prune_columns(self) -> None:
        checker = PermissionChecker()
        checker.set_acl(DatasetACL(
            dataset="ds", role="viewer",
            visible_columns=frozenset({"name", "age"}),
        ))
        table = _make_table(name=["Alice"], age=[30], ssn=["123-45-6789"])
        result = checker.apply_table_filter(table, dataset="ds", role="viewer")
        assert set(result.column_names) == {"name", "age"}
        assert result.num_rows == 1

    def test_all_columns_visible(self) -> None:
        checker = PermissionChecker()
        checker.set_acl(DatasetACL(
            dataset="ds", role="viewer",
            visible_columns=frozenset(),  # empty = all visible
        ))
        table = _make_table(a=[1], b=[2], c=[3])
        result = checker.apply_table_filter(table, dataset="ds", role="viewer")
        assert result.num_columns == 3

    def test_visible_columns_subset_not_in_table(self) -> None:
        checker = PermissionChecker()
        checker.set_acl(DatasetACL(
            dataset="ds", role="viewer",
            visible_columns=frozenset({"a", "nonexistent"}),
        ))
        table = _make_table(a=[1], b=[2])
        result = checker.apply_table_filter(table, dataset="ds", role="viewer")
        assert result.column_names == ["a"]

    def test_no_visible_columns_match(self) -> None:
        checker = PermissionChecker()
        checker.set_acl(DatasetACL(
            dataset="ds", role="viewer",
            visible_columns=frozenset({"x", "y"}),
        ))
        table = _make_table(a=[1], b=[2])
        result = checker.apply_table_filter(table, dataset="ds", role="viewer")
        assert result.num_rows == 0


class TestRowFilter:
    def test_simple_numeric_filter(self) -> None:
        checker = PermissionChecker()
        checker.set_acl(DatasetACL(
            dataset="ds", role="viewer",
            row_filter="age >= 18",
        ))
        table = _make_table(name=["Alice", "Bob", "Charlie"], age=[25, 12, 30])
        result = checker.apply_table_filter(table, dataset="ds", role="viewer")
        assert result.num_rows == 2
        assert result.column("name").to_pylist() == ["Alice", "Charlie"]

    def test_string_equality(self) -> None:
        checker = PermissionChecker()
        checker.set_acl(DatasetACL(
            dataset="ds", role="viewer",
            row_filter="region == US",
        ))
        table = _make_table(id=[1, 2, 3], region=["US", "EU", "US"])
        result = checker.apply_table_filter(table, dataset="ds", role="viewer")
        assert result.num_rows == 2

    def test_empty_filter(self) -> None:
        checker = PermissionChecker()
        checker.set_acl(DatasetACL(
            dataset="ds", role="viewer",
            row_filter="",
        ))
        table = _make_table(a=[1, 2, 3])
        result = checker.apply_table_filter(table, dataset="ds", role="viewer")
        assert result.num_rows == 3


class TestApplyRowFilterFunction:
    def test_numeric_comparison(self) -> None:
        table = _make_table(score=[5, 10, 15])
        result = _apply_row_filter(table, "score > 8")
        assert result.num_rows == 2

    def test_string_comparison(self) -> None:
        table = _make_table(status=["active", "inactive", "active"])
        result = _apply_row_filter(table, 'status == "active"')
        assert result.num_rows == 2

    def test_invalid_expression(self) -> None:
        table = _make_table(a=[1, 2])
        result = _apply_row_filter(table, "not a valid expression")
        assert result.num_rows == 0  # fail-closed (review H3, v1.10.7)

    def test_missing_column(self) -> None:
        table = _make_table(a=[1, 2])
        result = _apply_row_filter(table, "nonexistent > 5")
        assert result.num_rows == 0  # fail-closed (review H3, v1.10.7)

    def test_empty_table(self) -> None:
        table = _make_table(a=[])
        result = _apply_row_filter(table, "a > 5")
        assert result.num_rows == 0


class TestCombinedColumnAndRowFilter:
    def test_both_filters(self) -> None:
        checker = PermissionChecker()
        checker.set_acl(DatasetACL(
            dataset="ds", role="viewer",
            visible_columns=frozenset({"name", "age"}),
            row_filter="age >= 18",
        ))
        table = _make_table(name=["Alice", "Bob", "Charlie"], age=[25, 12, 30], ssn=["111", "222", "333"])
        result = checker.apply_table_filter(table, dataset="ds", role="viewer")
        assert set(result.column_names) == {"name", "age"}
        assert result.num_rows == 2
        assert "ssn" not in result.column_names


class TestAdminBypass:
    def test_admin_no_filter(self) -> None:
        checker = PermissionChecker()
        checker.set_acl(DatasetACL(
            dataset="ds", role="viewer",
            visible_columns=frozenset({"name"}),
        ))
        table = _make_table(name=["Alice"], ssn=["111"])
        result = checker.apply_table_filter(table, dataset="ds", role="admin")
        assert result.num_columns == 2  # admin sees all


class TestNoAclConfigured:
    def test_no_acl_returns_unchanged(self) -> None:
        checker = PermissionChecker()
        table = _make_table(a=[1, 2, 3], b=[4, 5, 6])
        result = checker.apply_table_filter(table, dataset="ds", role="viewer")
        assert result.num_columns == 2
        assert result.num_rows == 3
