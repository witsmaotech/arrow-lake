"""Tests for Lake facade new methods — DARMU integration (integration).

Tests:
- End-to-end: ingest CSV → list → query via Daft
- Daft lazy query with chained operations
- Catalog with version tracking
- Lake.from_yaml() end-to-end
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageConfig


@pytest.fixture()
def lake(tmp_path: Path) -> Lake:
    return Lake(base_uri=str(tmp_path / "lance_data"), config=ArrowLakeConfig(storage=StorageConfig(backend="local")))


@pytest.fixture()
def csv_file(tmp_path: Path) -> str:
    """Create a test CSV file."""
    csv_path = tmp_path / "test_data.csv"
    csv_path.write_text("id,name,score\n1,Alice,0.9\n2,Bob,0.7\n3,Carol,0.85\n")
    return str(csv_path)


@pytest.fixture()
def json_file(tmp_path: Path) -> str:
    """Create a test JSON file."""
    json_path = tmp_path / "test_data.json"
    json_path.write_text('[{"id":"1","val":10},{"id":"2","val":20}]')
    return str(json_path)


class TestIngestAndQuery:
    """End-to-end: ingest → list → query."""

    def test_ingest_csv_then_list(self, lake: Lake, csv_file: str) -> None:
        report = lake.ingest("csv_ds", [csv_file])
        assert report.total_rows >= 1
        assert "csv_ds" in lake.list_datasets()

    def test_ingest_json_then_query(self, lake: Lake, json_file: str) -> None:
        report = lake.ingest("json_ds", [json_file])
        assert report.total_rows >= 1
        result = lake.query("json_ds", "SELECT * FROM json_ds")
        assert result.row_count >= 1

    def test_ingest_then_catalog(self, lake: Lake, csv_file: str) -> None:
        lake.ingest("cat_ds", [csv_file])
        catalog = lake.catalog()
        assert catalog.total >= 1
        entry = next(e for e in catalog.datasets if e.name == "cat_ds")
        assert entry.num_rows >= 1
        assert entry.version >= 1


class TestDaftQueryIntegration:
    """Integration tests for Daft lazy query path."""

    def test_daft_select_filter_collect(self, lake: Lake) -> None:
        """Ingest → Daft load → select → filter → collect."""

        storage = lake._get_storage()
        table = pa.table(
            {
                "id": [1, 2, 3, 4, 5],
                "category": ["A", "B", "A", "B", "A"],
                "value": [10.0, 20.0, 30.0, 40.0, 50.0],
            }
        )
        storage.create_dataset("daft_test", table)

        frame = lake.daft_query("daft_test")
        result = frame.select("id", "category", "value").collect()
        assert isinstance(result, pa.Table)
        assert result.num_rows == 5
        assert "id" in result.column_names
        assert "value" in result.column_names

    def test_daft_filter_and_sort(self, lake: Lake) -> None:
        """Daft chained filter → sort → collect."""

        storage = lake._get_storage()
        table = pa.table(
            {
                "name": ["Alice", "Bob", "Carol"],
                "score": [0.9, 0.7, 0.85],
            }
        )
        storage.create_dataset("daft_sort", table)

        frame = lake.daft_query("daft_sort")
        result = frame.filter("score > 0.75").sort("score", desc=True).collect()
        assert isinstance(result, pa.Table)
        assert result.num_rows == 2
        scores = result.column("score").to_pylist()
        assert scores == sorted(scores, reverse=True)

    def test_daft_query_with_columns(self, lake: Lake) -> None:
        """Daft column projection."""

        storage = lake._get_storage()
        table = pa.table(
            {
                "a": [1, 2, 3],
                "b": ["x", "y", "z"],
                "c": [1.0, 2.0, 3.0],
            }
        )
        storage.create_dataset("daft_cols", table)

        frame = lake.daft_query("daft_cols", columns=["a", "c"])
        result = frame.collect()
        assert result.num_columns == 2
        assert result.column_names == ["a", "c"]


class TestCatalogWithVersions:
    """Test catalog with version tracking after append."""

    def test_catalog_shows_version_after_append(self, lake: Lake) -> None:

        storage = lake._get_storage()
        table1 = pa.table({"x": [1, 2]})
        table2 = pa.table({"x": [3, 4]})
        storage.create_dataset("versioned_ds", table1)
        storage.append_dataset("versioned_ds", table2)

        catalog = lake.catalog()
        entry = next(e for e in catalog.datasets if e.name == "versioned_ds")
        assert entry.version >= 2
        assert entry.num_rows == 4


class TestFromYamlEndToEnd:
    """Test Lake.from_yaml() with real operations."""

    def test_from_yaml_then_ingest(self, tmp_path: Path, csv_file: str) -> None:
        config_file = tmp_path / "e2e_config.yaml"
        config_file.write_text("storage:\n  backend: local\nolap:\n  max_result_rows: 5000\n")
        data_dir = tmp_path / "lance_data"
        lake = Lake.from_yaml(str(config_file), base_uri=str(data_dir))

        report = lake.ingest("yaml_ds", [csv_file])
        assert report.total_rows >= 1
        assert "yaml_ds" in lake.list_datasets()


class TestDeleteDatasetIntegration:
    """Test delete_dataset removes data from storage."""

    def test_delete_removes_from_catalog(self, lake: Lake) -> None:

        storage = lake._get_storage()
        table = pa.table({"a": [1]})
        storage.create_dataset("to_delete", table)

        assert "to_delete" in lake.list_datasets()
        lake.delete_dataset("to_delete")
        assert "to_delete" not in lake.list_datasets()
        assert lake.catalog().total == 0
