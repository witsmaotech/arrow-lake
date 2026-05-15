"""Unit tests for export_dataframe — Daft Phase 2, Sprint 8."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.exceptions import StorageError
from arrow_lake.ingest.storage import LanceStorageManager


@pytest.fixture
def mock_storage() -> MagicMock:
    s = MagicMock(spec=LanceStorageManager)
    # Remove spec to allow real method call via patch
    return s


class TestExportDataframe:
    def test_unsupported_format_raises(self) -> None:
        storage = MagicMock(spec=LanceStorageManager)
        storage._storage_options = None
        # Use real method
        real = LanceStorageManager.__new__(LanceStorageManager)
        real._storage_options = None
        with pytest.raises(ValueError, match="Unsupported export format"):
            real.export_dataframe(MagicMock(), "s3://out", "xml")

    @patch("daft.DataFrame")
    def test_parquet_export(self, MockDF: MagicMock) -> None:
        mock_df = MagicMock()
        mock_count = MagicMock()
        mock_arrow = MagicMock()
        mock_arrow.column.return_value = [MagicMock(as_py=MagicMock(return_value=100))]
        mock_count.to_arrow.return_value = mock_arrow
        mock_df.count.return_value = mock_count

        real = LanceStorageManager.__new__(LanceStorageManager)
        real._storage_options = None

        result = real.export_dataframe(mock_df, "s3://bucket/out/", "parquet")
        assert result["row_count"] == 100
        assert result["format"] == "parquet"
        mock_df.write_parquet.assert_called_once()

    @patch("daft.DataFrame")
    def test_csv_export(self, MockDF: MagicMock) -> None:
        mock_df = MagicMock()
        mock_count = MagicMock()
        mock_arrow = MagicMock()
        mock_arrow.column.return_value = [MagicMock(as_py=MagicMock(return_value=50))]
        mock_count.to_arrow.return_value = mock_arrow
        mock_df.count.return_value = mock_count

        real = LanceStorageManager.__new__(LanceStorageManager)
        real._storage_options = None

        result = real.export_dataframe(mock_df, "/tmp/out.csv", "csv")
        assert result["row_count"] == 50
        mock_df.write_csv.assert_called_once()

    def test_export_failure_raises_storage_error(self) -> None:
        mock_df = MagicMock()
        mock_df.write_parquet.side_effect = RuntimeError("disk full")

        real = LanceStorageManager.__new__(LanceStorageManager)
        real._storage_options = None

        with pytest.raises(StorageError, match="Export to parquet failed"):
            real.export_dataframe(mock_df, "/tmp/out", "parquet")
