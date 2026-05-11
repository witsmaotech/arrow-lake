"""Tests for _lake_query.py mixin — query, OLAP, export, materialize, daft."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageBackend, StorageConfig


@pytest.fixture
def lake(tmp_path: Path) -> Lake:
    cfg = ArrowLakeConfig()
    cfg.storage = StorageConfig(base_uri=str(tmp_path / "data"), backend=StorageBackend.LOCAL)
    return Lake(base_uri=str(tmp_path / "data"), config=cfg)


class TestQuery:
    def test_metadata_query(self, lake):
        mock_result = MagicMock()
        with patch("arrow_lake.query.metadata.MetadataSearchBridge") as MockBridge:
            MockBridge.return_value.query.return_value = mock_result
            result = lake.query("ds", "SELECT * FROM ds")
            assert result is mock_result

    def test_metadata_query_caches_bridge(self, lake):
        with patch("arrow_lake.query.metadata.MetadataSearchBridge") as MockBridge:
            MockBridge.return_value.query.return_value = MagicMock()
            lake.query("ds", "SELECT 1")
            lake.query("ds", "SELECT 2")
            assert MockBridge.call_count == 1


class TestOlapQuery:
    def test_olap_query_basic(self, lake):
        mock_result = MagicMock()
        mock_result.table = pa.table({"x": [1]})
        with patch("arrow_lake.query.olap.OlapSearchBridge") as MockBridge:
            MockBridge.return_value.query.return_value = mock_result
            result = lake.olap_query("ds", "SELECT COUNT(*) FROM ds")
            assert result is mock_result

    def test_olap_query_with_max_rows(self, lake):
        with patch("arrow_lake.query.olap.OlapSearchBridge") as MockBridge:
            MockBridge.return_value.query.return_value = MagicMock(table=pa.table({"x": [1]}))
            lake.olap_query("ds", "SELECT 1", max_rows=100)
            MockBridge.return_value.query.assert_called_once_with(
                "ds", "SELECT 1", max_rows=100, tables=None,
            )

    def test_olap_query_with_tables(self, lake):
        tables = {"extra": pa.table({"a": [1]})}
        with patch("arrow_lake.query.olap.OlapSearchBridge") as MockBridge:
            MockBridge.return_value.query.return_value = MagicMock(table=pa.table({"x": [1]}))
            lake.olap_query("ds", "SELECT 1", tables=tables)
            MockBridge.return_value.query.assert_called_once_with(
                "ds", "SELECT 1", max_rows=None, tables=tables,
            )


class TestSqlQuery:
    def test_sql_query_delegates_to_olap(self, lake):
        with patch.object(lake, "olap_query", return_value="result") as mock:
            lake.sql_query("ds", "SELECT 1", max_rows=50)
            mock.assert_called_once_with("ds", "SELECT 1", max_rows=50, tables=None)


class TestMaterialize:
    def test_materialize(self, lake):
        with patch("arrow_lake.query.olap.OlapSearchBridge") as MockBridge:
            MockBridge.return_value.materialize.return_value = 42
            result = lake.materialize("ds", "SELECT * FROM ds")
            assert result == 42

    def test_materialize_with_args(self, lake):
        with patch("arrow_lake.query.olap.OlapSearchBridge") as MockBridge:
            MockBridge.return_value.materialize.return_value = 10
            lake.materialize("ds", "SELECT 1", view_name="mv", ttl_days=7, max_join_rows=1000)
            MockBridge.return_value.materialize.assert_called_once_with(
                "ds", "SELECT 1", view_name="mv", ttl_days=7, max_join_rows=1000,
            )


class TestCleanupMaterialized:
    def test_cleanup(self, lake):
        with patch("arrow_lake.query.olap.OlapSearchBridge") as MockBridge:
            MockBridge.return_value.cleanup_materialized.return_value = ["t1", "t2"]
            result = lake.cleanup_materialized(ttl_days=30)
            assert result == ["t1", "t2"]


class TestExport:
    def test_export_basic(self, lake):
        with patch("arrow_lake.query.export.ExportBridge") as MockBridge:
            MockBridge.return_value.export.return_value = "result"
            result = lake.export("ds", "/tmp/out.parquet")
            assert result == "result"

    def test_export_with_all_args(self, lake):
        with patch("arrow_lake.query.export.ExportBridge") as MockBridge:
            MockBridge.return_value.export.return_value = "result"
            lake.export(
                "ds", "/tmp/out.csv", format="csv",
                columns=["a", "b"], version=3,
                compression="gzip", overwrite=True,
            )
            MockBridge.return_value.export.assert_called_once_with(
                "ds", "/tmp/out.csv", format="csv",
                columns=["a", "b"], version=3,
                compression="gzip", overwrite=True,
            )


class TestDaftQuery:
    def test_daft_query(self, lake):
        with patch("arrow_lake.query.daft_api.DaftQueryEngine") as MockEngine:
            MockEngine.return_value.load.return_value = "frame"
            result = lake.daft_query("ds", columns=["a", "b"])
            assert result == "frame"

    def test_daft_query_no_columns(self, lake):
        with patch("arrow_lake.query.daft_api.DaftQueryEngine") as MockEngine:
            MockEngine.return_value.load.return_value = "frame"
            lake.daft_query("ds")
            MockEngine.return_value.load.assert_called_once_with("ds", columns=None)
