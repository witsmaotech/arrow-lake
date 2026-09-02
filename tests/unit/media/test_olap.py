"""Tests for arrow_lake.query.olap — Story 5.4.

Tests OlapSearchBridge:
- DTO frozen dataclass
- query (success, GROUP BY, HAVING, ORDER BY, window functions, LIMIT, COUNT(*))
- SQL validation (SELECT-only, dangerous keywords, semicolons, empty query)
- explain
- Config-driven defaults
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from arrow_lake.exceptions import QueryError
from arrow_lake.query.olap import OlapQueryResult, OlapSearchBridge

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_storage() -> MagicMock:
    """Create a mock LanceStorageManager that supports streaming."""
    storage = MagicMock()

    # scan_dataset returns the same data as read_dataset but as RecordBatchReader
    def _scan_as_reader(*args: object, **kwargs: object) -> pa.RecordBatchReader:
        table = storage.read_dataset.return_value
        if table is None:
            table = pa.table({})
        return table.to_reader()

    storage.scan_dataset.side_effect = _scan_as_reader
    return storage


def _make_sample_table(rows: int = 100) -> pa.Table:
    """Create a sample Arrow table for testing OLAP queries."""
    return pa.table(
        {
            "id": [f"doc-{i}" for i in range(rows)],
            "modality": ["text" if i % 2 == 0 else "image" for i in range(rows)],
            "source": [f"source-{i % 5}" for i in range(rows)],
            "text_content": [f"content {i}" for i in range(rows)],
        }
    )


# ---------------------------------------------------------------------------
# DTO Tests
# ---------------------------------------------------------------------------


class TestOlapQueryResult:
    """Test OlapQueryResult frozen dataclass."""

    def test_is_frozen(self) -> None:
        table = pa.table({"col1": [1, 2]})
        result = OlapQueryResult(
            table=table,
            row_count=2,
            column_count=1,
            sql="SELECT * FROM data",
        )
        with pytest.raises(FrozenInstanceError):
            result.row_count = 5  # type: ignore[misc]

    def test_all_fields_accessible(self) -> None:
        table = pa.table({"col1": [1], "col2": ["a"]})
        result = OlapQueryResult(
            table=table,
            row_count=1,
            column_count=2,
            sql="SELECT col1, col2 FROM data",
        )
        assert result.row_count == 1
        assert result.column_count == 2
        assert result.sql == "SELECT col1, col2 FROM data"
        assert result.table.num_rows == 1


# ---------------------------------------------------------------------------
# SQL Validation Tests
# ---------------------------------------------------------------------------


class TestSQLValidation:
    """Test OlapSearchBridge._validate_sql method."""

    def _bridge(self) -> OlapSearchBridge:
        return OlapSearchBridge(storage=MagicMock())

    def test_select_is_allowed(self) -> None:
        """SELECT queries should pass validation."""
        bridge = self._bridge()
        bridge._validate_sql("SELECT * FROM data")
        bridge._validate_sql("SELECT col1, SUM(col2) FROM data GROUP BY col1")

    def test_non_select_raises(self) -> None:
        """Non-SELECT queries should raise QueryError."""
        bridge = self._bridge()
        with pytest.raises(QueryError, match="SELECT"):
            bridge._validate_sql("INSERT INTO data VALUES (1)")

        with pytest.raises(QueryError, match="SELECT"):
            bridge._validate_sql("UPDATE data SET col1 = 1")

        with pytest.raises(QueryError, match="SELECT"):
            bridge._validate_sql("DELETE FROM data")

        with pytest.raises(QueryError, match="SELECT"):
            bridge._validate_sql("DROP TABLE data")

    def test_dangerous_keywords_blocked(self) -> None:
        """Dangerous SQL keywords should be blocked."""
        bridge = self._bridge()
        dangerous_queries = [
            "SELECT * FROM data; DROP TABLE data",
            "SELECT * FROM data WHERE 1=1; INSERT INTO data VALUES (1)",
            "SELECT * FROM data /* comment */ CREATE TABLE evil",
        ]
        for sql in dangerous_queries:
            with pytest.raises(QueryError, match="not allowed"):
                bridge._validate_sql(sql)

    def test_semicolons_blocked(self) -> None:
        """Semicolons should be blocked (single statement only)."""
        bridge = self._bridge()
        with pytest.raises(QueryError, match="Semicolon"):
            bridge._validate_sql("SELECT * FROM data;")

    def test_empty_query_raises(self) -> None:
        """Empty queries should raise QueryError."""
        bridge = self._bridge()
        with pytest.raises(QueryError, match="empty"):
            bridge._validate_sql("")

        with pytest.raises(QueryError, match="empty"):
            bridge._validate_sql("   ")

    def test_select_with_window_function_allowed(self) -> None:
        """Window functions should be allowed."""
        bridge = self._bridge()
        bridge._validate_sql(
            "SELECT *, ROW_NUMBER() OVER (PARTITION BY modality ORDER BY id) as rn FROM data"
        )

    def test_keywords_in_column_names_allowed(self) -> None:
        """Keywords as part of column names (word boundary) should be allowed."""
        bridge = self._bridge()
        # 'executive' contains 'EXEC' but is not a standalone keyword
        bridge._validate_sql("SELECT executive_name FROM data")
        # 'updated_at' contains 'UPDATE' but is not a standalone keyword
        bridge._validate_sql("SELECT updated_at FROM data")
        # 'description' is fine
        bridge._validate_sql("SELECT description FROM data")

    def test_union_of_selects_allowed(self) -> None:
        """v1.10.8+: AST-based validation — UNION of SELECTs is read-only, allowed."""
        bridge = self._bridge()
        bridge._validate_sql("SELECT * FROM data UNION SELECT * FROM other")

    def test_except_of_selects_allowed(self) -> None:
        """v1.10.8+: EXCEPT of SELECTs is read-only, allowed."""
        bridge = self._bridge()
        bridge._validate_sql("SELECT * FROM data EXCEPT SELECT * FROM other")


# ---------------------------------------------------------------------------
# query Tests
# ---------------------------------------------------------------------------


class TestQuery:
    """Test OlapSearchBridge.query."""

    def test_query_success(self) -> None:
        """Happy path: SELECT * returns the table."""
        storage = _make_mock_storage()
        sample_table = _make_sample_table(10)
        storage.read_dataset.return_value = sample_table

        bridge = OlapSearchBridge(storage)
        result = bridge.query("test_ds", "SELECT * FROM test_ds")

        assert isinstance(result, OlapQueryResult)
        assert result.row_count == 10
        assert result.column_count == 4
        assert result.sql == "SELECT * FROM test_ds LIMIT 100000"

    def test_query_group_by(self) -> None:
        """GROUP BY aggregation works."""
        storage = _make_mock_storage()
        storage.read_dataset.return_value = _make_sample_table(100)

        bridge = OlapSearchBridge(storage)
        result = bridge.query(
            "test_ds", "SELECT modality, COUNT(*) as cnt FROM test_ds GROUP BY modality"
        )

        assert result.row_count == 2  # text, image
        assert "cnt" in result.table.column_names

    def test_query_having(self) -> None:
        """HAVING clause filters aggregated results."""
        storage = _make_mock_storage()
        storage.read_dataset.return_value = _make_sample_table(100)

        bridge = OlapSearchBridge(storage)
        result = bridge.query(
            "test_ds",
            "SELECT modality, COUNT(*) as cnt FROM test_ds GROUP BY modality HAVING cnt > 40",
        )

        assert result.row_count > 0

    def test_query_order_by(self) -> None:
        """ORDER BY sorts results."""
        storage = _make_mock_storage()
        storage.read_dataset.return_value = _make_sample_table(100)

        bridge = OlapSearchBridge(storage)
        result = bridge.query(
            "test_ds",
            "SELECT source, COUNT(*) as cnt FROM test_ds GROUP BY source ORDER BY cnt DESC",
        )

        counts = result.table.column("cnt").to_pylist()
        assert counts == sorted(counts, reverse=True)

    def test_query_limit(self) -> None:
        """LIMIT truncates results."""
        storage = _make_mock_storage()
        storage.read_dataset.return_value = _make_sample_table(100)

        bridge = OlapSearchBridge(storage)
        result = bridge.query("test_ds", "SELECT * FROM test_ds LIMIT 5")

        assert result.row_count == 5

    def test_query_count_star(self) -> None:
        """COUNT(*) returns single row."""
        storage = _make_mock_storage()
        storage.read_dataset.return_value = _make_sample_table(100)

        bridge = OlapSearchBridge(storage)
        result = bridge.query("test_ds", "SELECT COUNT(*) as total FROM test_ds")

        assert result.row_count == 1

    def test_query_window_function(self) -> None:
        """Window function ROW_NUMBER works."""
        storage = _make_mock_storage()
        storage.read_dataset.return_value = _make_sample_table(50)

        bridge = OlapSearchBridge(storage)
        result = bridge.query(
            "test_ds",
            "SELECT *, ROW_NUMBER() OVER (PARTITION BY modality ORDER BY id) as rn FROM test_ds",
        )

        assert result.row_count == 50
        assert "rn" in result.table.column_names

    def test_query_max_rows_enforced(self) -> None:
        """max_rows limits result rows."""
        from arrow_lake.config import OlapConfig

        storage = _make_mock_storage()
        storage.read_dataset.return_value = _make_sample_table(100)

        config = OlapConfig(max_result_rows=5)
        bridge = OlapSearchBridge(storage, config=config)
        result = bridge.query("test_ds", "SELECT * FROM test_ds LIMIT 100")

        assert result.row_count <= 5

    def test_query_dataset_not_found(self) -> None:
        """Non-existent dataset raises QueryError."""
        storage = _make_mock_storage()
        storage.read_dataset.side_effect = FileNotFoundError("Dataset not found")
        storage.scan_dataset.side_effect = FileNotFoundError("Dataset not found")

        bridge = OlapSearchBridge(storage)
        with pytest.raises(QueryError, match="Failed to read"):
            bridge.query("missing_ds", "SELECT * FROM missing_ds")

    def test_query_invalid_sql_raises(self) -> None:
        """Invalid SQL raises QueryError."""
        storage = _make_mock_storage()
        bridge = OlapSearchBridge(storage)
        with pytest.raises(QueryError):
            bridge.query("test_ds", "INVALID SQL HERE")

    def test_query_max_rows_parameter_overrides_config(self) -> None:
        """Explicit max_rows parameter overrides config."""
        storage = _make_mock_storage()
        storage.read_dataset.return_value = _make_sample_table(100)

        bridge = OlapSearchBridge(storage)
        result = bridge.query("test_ds", "SELECT * FROM test_ds LIMIT 100", max_rows=3)

        assert result.row_count <= 3


# ---------------------------------------------------------------------------
# explain Tests
# ---------------------------------------------------------------------------


class TestExplain:
    """Test OlapSearchBridge.explain."""

    def test_explain_returns_string(self) -> None:
        """EXPLAIN returns a non-empty string."""
        storage = _make_mock_storage()
        storage.read_dataset.return_value = _make_sample_table(10)

        bridge = OlapSearchBridge(storage)
        result = bridge.explain("test_ds", "SELECT * FROM test_ds")

        assert isinstance(result, str)
        assert len(result) > 0

    def test_explain_validates_sql(self) -> None:
        """EXPLAIN also validates SQL."""
        storage = _make_mock_storage()
        bridge = OlapSearchBridge(storage)

        with pytest.raises(QueryError):
            bridge.explain("test_ds", "DROP TABLE data")


# ---------------------------------------------------------------------------
# Config-driven defaults
# ---------------------------------------------------------------------------


class TestConfigDrivenDefaults:
    """Test OlapConfig drives bridge defaults."""

    def test_config_drives_max_result_rows(self) -> None:
        from arrow_lake.config import OlapConfig

        storage = _make_mock_storage()
        config = OlapConfig(max_result_rows=5000)
        bridge = OlapSearchBridge(storage, config=config)

        assert bridge._config.max_result_rows == 5000

    def test_no_config_uses_defaults(self) -> None:
        storage = _make_mock_storage()
        bridge = OlapSearchBridge(storage)

        assert bridge._config.max_result_rows == 100_000
        # enable_predicate_pushdown was removed in the v1.10.x 配置精简 (dead field).


# ---------------------------------------------------------------------------
# Dataset name validation
# ---------------------------------------------------------------------------


class TestDatasetNameValidation:
    """Test dataset name validation for safety."""

    def test_valid_name_accepted(self) -> None:
        storage = _make_mock_storage()
        storage.read_dataset.return_value = _make_sample_table(10)

        bridge = OlapSearchBridge(storage)
        result = bridge.query("valid_dataset", "SELECT * FROM valid_dataset")
        assert result.row_count == 10

    def test_name_with_hyphen_accepted(self) -> None:
        storage = _make_mock_storage()
        storage.read_dataset.return_value = _make_sample_table(10)

        bridge = OlapSearchBridge(storage)
        result = bridge.query("my-dataset", 'SELECT * FROM "my-dataset"')
        assert result.row_count == 10

    def test_invalid_name_raises(self) -> None:
        storage = _make_mock_storage()
        bridge = OlapSearchBridge(storage)

        with pytest.raises(ValueError, match="Invalid identifier"):
            bridge.query("../etc/passwd", 'SELECT * FROM "../etc/passwd"')

        with pytest.raises(ValueError, match="Invalid identifier"):
            bridge.query("name; DROP TABLE", "SELECT * FROM data")

        with pytest.raises(ValueError, match="Invalid identifier"):
            bridge.query("", "SELECT * FROM data")
