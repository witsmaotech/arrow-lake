"""Tests for Story 7.6 — SQL Query Support (JOIN, Daft SQL placeholder)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from arrow_lake.config import OlapConfig
from arrow_lake.exceptions import ErrorCode, QueryError
from arrow_lake.query.olap import OlapQueryResult, OlapSearchBridge


def _make_bridge(
    enable_join: bool = True,
    max_rows: int = 100_000,
) -> OlapSearchBridge:
    """Create a bridge with a mock storage."""
    storage = MagicMock()

    def _scan_as_reader(*args: object, **kwargs: object) -> pa.RecordBatchReader:
        table = storage.read_dataset.return_value
        if table is None:
            table = pa.table({})
        return table.to_reader()

    storage.scan_dataset.side_effect = _scan_as_reader
    config = OlapConfig(max_result_rows=max_rows, enable_join=enable_join)
    return OlapSearchBridge(storage=storage, config=config)


def _make_table(rows: list[dict[str, object]], schema: pa.Schema | None = None) -> pa.Table:
    """Create a PyArrow table from row dicts."""
    if schema is None:
        schema = pa.schema(
            [
                pa.field("id", pa.int64()),
                pa.field("name", pa.utf8()),
                pa.field("value", pa.float64()),
            ]
        )
    return pa.Table.from_pylist(rows, schema=schema)


class TestToArrow:
    """Test OlapQueryResult.to_arrow() convenience method."""

    def test_to_arrow_returns_table(self) -> None:
        table = _make_table([{"id": 1, "name": "a", "value": 1.0}])
        result = OlapQueryResult(table=table, row_count=1, column_count=3, sql="SELECT * FROM t")
        arrow = result.to_arrow()
        assert isinstance(arrow, pa.Table)
        assert arrow.num_rows == 1

    def test_to_arrow_is_same_table(self) -> None:
        table = _make_table([{"id": 1, "name": "a", "value": 1.0}])
        result = OlapQueryResult(table=table, row_count=1, column_count=3, sql="SELECT * FROM t")
        assert result.to_arrow() is table


class TestSQLJoinValidation:
    """Test JOIN SQL validation."""

    def test_join_sql_is_allowed_by_default(self) -> None:
        bridge = _make_bridge(enable_join=True)
        # Should NOT raise
        bridge._validate_sql("SELECT a.id, b.name FROM data a JOIN data b ON a.id = b.id")

    def test_join_disabled_rejects_join_keyword(self) -> None:
        bridge = _make_bridge(enable_join=False)
        with pytest.raises(QueryError, match="JOIN"):
            bridge._validate_sql("SELECT a.id FROM data a JOIN data b ON a.id = b.id")

    def test_join_disabled_rejects_inner_join(self) -> None:
        bridge = _make_bridge(enable_join=False)
        with pytest.raises(QueryError, match="JOIN"):
            bridge._validate_sql("SELECT * FROM data a INNER JOIN data b ON a.id = b.id")

    def test_join_disabled_rejects_left_join(self) -> None:
        bridge = _make_bridge(enable_join=False)
        with pytest.raises(QueryError, match="JOIN"):
            bridge._validate_sql("SELECT * FROM data a LEFT JOIN data b ON a.id = b.id")

    def test_join_disabled_rejects_cross_join(self) -> None:
        bridge = _make_bridge(enable_join=False)
        with pytest.raises(QueryError, match="JOIN"):
            bridge._validate_sql("SELECT * FROM data a CROSS JOIN data b")

    def test_join_disabled_allows_simple_select(self) -> None:
        bridge = _make_bridge(enable_join=False)
        bridge._validate_sql("SELECT id, name FROM data WHERE value > 10")

    def test_join_disabled_allows_group_by(self) -> None:
        bridge = _make_bridge(enable_join=False)
        bridge._validate_sql("SELECT modality, COUNT(*) FROM data GROUP BY modality")

    def test_join_disabled_uses_correct_error_code(self) -> None:
        bridge = _make_bridge(enable_join=False)
        with pytest.raises(QueryError) as exc_info:
            bridge._validate_sql("SELECT * FROM data a JOIN data b ON a.id = b.id")
        assert exc_info.value.error_code == ErrorCode.QUERY_JOIN_NOT_ALLOWED


class TestMultiTableRegister:
    """Test multi-table query support."""

    def test_query_with_extra_tables(self) -> None:
        bridge = _make_bridge()
        table_a = _make_table([{"id": 1, "name": "a", "value": 10.0}])
        table_b = _make_table([{"id": 1, "name": "b", "value": 20.0}])
        bridge._storage.read_dataset.return_value = table_a

        extra_tables = {"other": table_b}
        result = bridge.query(
            "data",
            "SELECT data.name, other.value FROM data JOIN other ON data.id = other.id",
            tables=extra_tables,
        )
        assert result.row_count == 1

    def test_query_without_extra_tables(self) -> None:
        bridge = _make_bridge()
        table = _make_table([{"id": 1, "name": "a", "value": 10.0}])
        bridge._storage.read_dataset.return_value = table

        result = bridge.query("data", "SELECT id, name FROM data WHERE value > 5")
        assert result.row_count == 1

    def test_extra_table_name_validation(self) -> None:
        bridge = _make_bridge()
        table = _make_table([{"id": 1, "name": "a", "value": 10.0}])
        bridge._storage.read_dataset.return_value = table

        # Invalid table name in extra_tables
        with pytest.raises(ValueError, match="Invalid identifier"):
            bridge.query(
                "data",
                "SELECT * FROM data",
                tables={"../etc/passwd": table},
            )

    def test_empty_extra_tables_dict(self) -> None:
        bridge = _make_bridge()
        table = _make_table([{"id": 1, "name": "a", "value": 10.0}])
        bridge._storage.read_dataset.return_value = table

        result = bridge.query("data", "SELECT COUNT(*) FROM data", tables={})
        assert result.row_count == 1


class TestSQLValidationPassesForJoin:
    """Test that JOIN syntax passes through the standard SQL validator when enabled."""

    def test_join_with_where(self) -> None:
        bridge = _make_bridge(enable_join=True)
        bridge._validate_sql("SELECT a.id FROM data a JOIN data b ON a.id = b.id WHERE a.value > 5")

    def test_join_with_group_by(self) -> None:
        bridge = _make_bridge(enable_join=True)
        bridge._validate_sql(
            "SELECT a.modality, COUNT(*) FROM data a JOIN data b ON a.id = b.id GROUP BY a.modality"
        )

    def test_union_still_blocked(self) -> None:
        bridge = _make_bridge(enable_join=True)
        with pytest.raises(QueryError, match="UNION"):
            bridge._validate_sql("SELECT id FROM data UNION SELECT id FROM data")

    def test_subquery_join(self) -> None:
        bridge = _make_bridge(enable_join=True)
        bridge._validate_sql(
            "SELECT * FROM data a JOIN (SELECT id FROM data WHERE value > 10) b ON a.id = b.id"
        )


class TestOlapConfigEnableJoin:
    """Test OlapConfig.enable_join field."""

    def test_default_is_true(self) -> None:
        config = OlapConfig()
        assert config.enable_join is True

    def test_can_set_false(self) -> None:
        config = OlapConfig(enable_join=False)
        assert config.enable_join is False

    def test_bridge_reads_config(self) -> None:
        bridge = _make_bridge(enable_join=False)
        assert bridge._config.enable_join is False
