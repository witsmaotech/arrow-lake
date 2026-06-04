"""Tests for MetadataSearchBridge — metadata SQL query engine."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from arrow_lake.exceptions import QueryError
from arrow_lake.query.metadata import MetadataQueryResult


@pytest.fixture
def mock_storage() -> MagicMock:
    s = MagicMock()
    tbl = pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"]})
    s.scan_dataset.return_value = tbl
    s.dataset_uri.return_value = "/data/test.lance"
    return s


@pytest.fixture
def bridge(mock_storage: MagicMock) -> object:
    from arrow_lake.query.metadata import MetadataSearchBridge
    return MetadataSearchBridge(storage=mock_storage)


# ── Validation ──


class TestValidation:
    def test_rejects_non_select(self, bridge: object) -> None:
        with pytest.raises(QueryError, match="SELECT"):
            bridge.query("ds1", "DROP TABLE ds1")

    def test_rejects_empty_sql(self, bridge: object) -> None:
        with pytest.raises(QueryError):
            bridge.query("ds1", "")

    def test_rejects_dangerous_keywords(self, bridge: object) -> None:
        with pytest.raises(QueryError, match="not allowed"):
            bridge.query("ds1", "SELECT * FROM ds1; DROP TABLE ds1")

    def test_rejects_semicolons(self, bridge: object) -> None:
        with pytest.raises(QueryError, match="Semicolons"):
            bridge.query("ds1", "SELECT * FROM ds1;")

    def test_rejects_invalid_dataset_name(self, bridge: object) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            bridge.query("../../evil", "SELECT * FROM t")


# ── Query execution ──


class TestQuery:
    def test_basic_query(self, bridge: object) -> None:
        result = bridge.query("ds1", "SELECT * FROM ds1 LIMIT 10")
        assert isinstance(result, MetadataQueryResult)
        assert result.row_count >= 0

    def test_query_with_tables(self, bridge: object, mock_storage: MagicMock) -> None:
        extra = pa.table({"x": [10, 20]})
        result = bridge.query("ds1", "SELECT * FROM ds1", tables={"extra": extra})
        assert isinstance(result, MetadataQueryResult)

    def test_dataset_not_found(self, bridge: object, mock_storage: MagicMock) -> None:
        from arrow_lake.exceptions import ErrorCode, StorageError
        mock_storage.scan_dataset.side_effect = StorageError(
            error_code=ErrorCode.STORAGE_READ_FAILED, message="not found",
        )
        with pytest.raises(QueryError, match="Failed to read"):
            bridge.query("ds1", "SELECT * FROM ds1")


# ── MetadataQueryResult ──


class TestMetadataQueryResult:
    def test_to_arrow(self) -> None:
        tbl = pa.table({"x": [1]})
        r = MetadataQueryResult(table=tbl, row_count=1, column_count=1, sql="SELECT 1")
        assert r.to_arrow() is tbl


# ── _relational_query ──


class TestRelationalQuery:
    def test_basic_relational(self, bridge: object, mock_storage: MagicMock) -> None:
        tbl = pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"]})
        mock_storage.scan_dataset.return_value = tbl
        result = bridge._relational_query("ds1", ["id", "name"])
        result_tbl = result.read_all() if hasattr(result, "read_all") else result
        assert result_tbl.num_columns == 2

    def test_relational_with_where(self, bridge: object, mock_storage: MagicMock) -> None:
        tbl = pa.table({"id": [1, 2, 3], "value": [10, 20, 30]})
        mock_storage.scan_dataset.return_value = tbl
        result = bridge._relational_query("ds1", ["id", "value"], where="value > 15")
        result_tbl = result.read_all() if hasattr(result, "read_all") else result
        assert result_tbl.num_rows == 2

    def test_relational_invalid_dataset(self, bridge: object) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            bridge._relational_query("../../evil", ["id"])

    def test_relational_invalid_column(self, bridge: object) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            bridge._relational_query("ds1", ["; DROP TABLE"])
