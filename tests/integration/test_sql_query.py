"""Integration tests for Story 7.6 — SQL Query JOIN support."""

from __future__ import annotations

import pyarrow as pa
from arrow_lake.ingest.storage import LanceStorageManager
from arrow_lake.query.olap import OlapSearchBridge


def _create_test_dataset(
    tmp_path: str,
    name: str = "test_ds",
    rows: int = 100,
) -> str:
    """Create a Lance test dataset with id, modality, source, quality_score, value."""
    storage = LanceStorageManager(str(tmp_path))
    schema = pa.schema(
        [
            pa.field("id", pa.int64()),
            pa.field("modality", pa.utf8()),
            pa.field("source", pa.utf8()),
            pa.field("quality_score", pa.float64()),
            pa.field("value", pa.float64()),
        ]
    )
    data = {
        "id": list(range(rows)),
        "modality": ["image" if i % 2 == 0 else "text" for i in range(rows)],
        "source": ["web" if i % 3 == 0 else "api" if i % 3 == 1 else "file" for i in range(rows)],
        "quality_score": [0.5 + (i % 10) * 0.05 for i in range(rows)],
        "value": [float(i * 1.5) for i in range(rows)],
    }
    table = pa.Table.from_pydict(data, schema=schema)
    storage.create_dataset(name, table)
    return name


class TestSQLJoin:
    """Test JOIN queries between registered tables."""

    def test_self_join(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path)
        storage = LanceStorageManager(str(tmp_path))
        bridge = OlapSearchBridge(storage)

        result = bridge.query(
            ds_name,
            "SELECT a.id, b.id as b_id FROM test_ds a JOIN test_ds b ON a.id = b.id WHERE a.id < 5",
        )
        assert result.row_count >= 1
        assert result.column_count >= 2

    def test_join_with_group_by(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path)
        storage = LanceStorageManager(str(tmp_path))
        bridge = OlapSearchBridge(storage)

        result = bridge.query(
            ds_name,
            "SELECT a.modality, COUNT(*) as cnt FROM test_ds a "
            "JOIN test_ds b ON a.id = b.id GROUP BY a.modality",
        )
        assert result.row_count >= 1
        # Should have modality and cnt columns
        col_names = {result.table.schema.field(i).name for i in range(result.column_count)}
        assert "modality" in col_names
        assert "cnt" in col_names

    def test_join_with_having(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path)
        storage = LanceStorageManager(str(tmp_path))
        bridge = OlapSearchBridge(storage)

        result = bridge.query(
            ds_name,
            "SELECT a.modality, COUNT(*) as cnt FROM test_ds a "
            "JOIN test_ds b ON a.id = b.id "
            "GROUP BY a.modality HAVING cnt > 0",
        )
        assert result.row_count >= 1

    def test_join_with_order_by_limit(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path)
        storage = LanceStorageManager(str(tmp_path))
        bridge = OlapSearchBridge(storage)

        result = bridge.query(
            ds_name,
            "SELECT a.id, a.value FROM test_ds a "
            "JOIN test_ds b ON a.id = b.id "
            "ORDER BY a.value DESC LIMIT 5",
        )
        assert result.row_count == 5


class TestMultiTableRegister:
    """Test extra tables parameter for multi-table JOIN."""

    def test_join_with_extra_table(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path)
        storage = LanceStorageManager(str(tmp_path))
        bridge = OlapSearchBridge(storage)

        # Create an extra Arrow table
        extra_schema = pa.schema([pa.field("id", pa.int64()), pa.field("tag", pa.utf8())])
        extra_table = pa.Table.from_pydict(
            {"id": [0, 1, 2, 3, 4], "tag": ["a", "b", "c", "d", "e"]},
            schema=extra_schema,
        )

        result = bridge.query(
            ds_name,
            "SELECT test_ds.id, test_ds.modality, tags.tag FROM test_ds "
            "JOIN tags ON test_ds.id = tags.id WHERE test_ds.id < 3",
            tables={"tags": extra_table},
        )
        assert result.row_count == 3
        col_names = {result.table.schema.field(i).name for i in range(result.column_count)}
        assert "tag" in col_names
        assert "modality" in col_names

    def test_left_join_with_extra_table(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path)
        storage = LanceStorageManager(str(tmp_path))
        bridge = OlapSearchBridge(storage)

        extra_table = pa.Table.from_pydict(
            {"id": [0, 1], "tag": ["a", "b"]},
            schema=pa.schema([pa.field("id", pa.int64()), pa.field("tag", pa.utf8())]),
        )

        result = bridge.query(
            ds_name,
            "SELECT test_ds.id, tags.tag FROM test_ds LEFT JOIN tags ON test_ds.id = tags.id LIMIT 5",
            tables={"tags": extra_table},
        )
        assert result.row_count == 5


class TestSubquery:
    """Test subquery support."""

    def test_subquery_in_where(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path)
        storage = LanceStorageManager(str(tmp_path))
        bridge = OlapSearchBridge(storage)

        result = bridge.query(
            ds_name,
            "SELECT id, value FROM test_ds WHERE value > "
            "(SELECT AVG(value) FROM test_ds) ORDER BY value LIMIT 5",
        )
        assert result.row_count == 5

    def test_subquery_in_from(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path)
        storage = LanceStorageManager(str(tmp_path))
        bridge = OlapSearchBridge(storage)

        result = bridge.query(
            ds_name,
            "SELECT modality, avg_val FROM "
            "(SELECT modality, AVG(value) as avg_val FROM test_ds GROUP BY modality) sub "
            "WHERE avg_val > 0",
        )
        assert result.row_count >= 1


class TestComplexOLAP:
    """Test complex OLAP queries combining multiple features."""

    def test_window_function(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path)
        storage = LanceStorageManager(str(tmp_path))
        bridge = OlapSearchBridge(storage)

        result = bridge.query(
            ds_name,
            "SELECT id, value, ROW_NUMBER() OVER (ORDER BY value DESC) as rn FROM test_ds LIMIT 5",
        )
        assert result.row_count == 5

    def test_case_when(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path)
        storage = LanceStorageManager(str(tmp_path))
        bridge = OlapSearchBridge(storage)

        result = bridge.query(
            ds_name,
            "SELECT modality, "
            "CASE WHEN quality_score > 0.8 THEN 'high' ELSE 'low' END as tier, "
            "COUNT(*) as cnt "
            "FROM test_ds GROUP BY modality, tier ORDER BY modality",
        )
        assert result.row_count >= 1
        col_names = {result.table.schema.field(i).name for i in range(result.column_count)}
        assert "tier" in col_names
