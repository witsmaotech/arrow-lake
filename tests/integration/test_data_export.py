"""Integration tests for data export — Story 5.9."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from arrow_lake.ingest.storage import LanceStorageManager
from arrow_lake.query.export import ExportBridge

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def storage(tmp_path: Path) -> LanceStorageManager:
    return LanceStorageManager(str(tmp_path / "lance_data"))


def _create_sample_dataset(
    storage: LanceStorageManager,
    name: str = "export_test",
    n: int = 10,
) -> None:
    """Create a sample Lance dataset for export tests."""
    table = pa.table(
        {
            "id": list(range(n)),
            "text_content": [f"document-{i}" for i in range(n)],
            "score": [float(i * 10) for i in range(n)],
            "modality": ["text"] * n,
            "image_data": [f"img-{i}".encode() for i in range(n)],
        }
    )
    storage.create_dataset(name, table)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExportParquetIntegration:
    """Integration: Export Lance dataset to Parquet."""

    def test_export_to_parquet(self, storage: LanceStorageManager, tmp_path: Path) -> None:
        """Export full dataset to Parquet and verify contents."""
        _create_sample_dataset(storage, n=10)
        output = str(tmp_path / "output.parquet")

        bridge = ExportBridge(storage)
        result = bridge.export("export_test", output)

        assert result.format == "parquet"
        assert result.row_count == 10
        assert result.column_count == 5  # all columns including image_data
        assert result.file_size_bytes > 0

        # Verify we can read the Parquet file back
        table = pq.read_table(output)
        assert table.num_rows == 10

    def test_export_column_subset(self, storage: LanceStorageManager, tmp_path: Path) -> None:
        """Export only selected columns to Parquet."""
        _create_sample_dataset(storage, n=5)
        output = str(tmp_path / "subset.parquet")

        bridge = ExportBridge(storage)
        result = bridge.export("export_test", output, columns=["id", "text_content"])

        assert result.column_count == 2
        table = pq.read_table(output)
        assert set(table.column_names) == {"id", "text_content"}

    def test_export_specific_version(self, storage: LanceStorageManager, tmp_path: Path) -> None:
        """Export a specific dataset version."""
        _create_sample_dataset(storage, n=5)

        # Append more data to create version 2
        extra = pa.table(
            {
                "id": [10, 11],
                "text_content": ["doc-10", "doc-11"],
                "score": [100.0, 110.0],
                "modality": ["text", "text"],
                "image_data": [b"img-10", b"img-11"],
            }
        )
        storage.append_dataset("export_test", extra)

        output = str(tmp_path / "v1.parquet")
        bridge = ExportBridge(storage)
        result = bridge.export("export_test", output, version=1)

        assert result.row_count == 5  # only version 1 data


class TestExportCSVIntegration:
    """Integration: Export Lance dataset to CSV."""

    def test_export_to_csv_excludes_binary(
        self, storage: LanceStorageManager, tmp_path: Path
    ) -> None:
        """Export to CSV should exclude binary columns."""
        _create_sample_dataset(storage, n=5)
        output = str(tmp_path / "output.csv")

        bridge = ExportBridge(storage)
        result = bridge.export("export_test", output)

        assert result.format == "csv"
        assert result.column_count == 4  # image_data excluded
        assert result.row_count == 5

        # Read CSV back and verify no image_data column
        import pyarrow.csv as csv

        table = csv.read_csv(output)
        assert "image_data" not in table.column_names


class TestExportSDKIntegration:
    """Integration: Lake.export() SDK method."""

    def test_lake_export_sdk(self, storage: LanceStorageManager, tmp_path: Path) -> None:
        """Lake.export() end-to-end with real Lance storage."""
        from arrow_lake import Lake

        _create_sample_dataset(storage, n=5)
        output = str(tmp_path / "sdk_export.parquet")

        lake = Lake(base_uri=storage.base_uri)
        result = lake.export("export_test", output)

        assert result.format == "parquet"
        assert result.row_count == 5
        assert result.file_size_bytes > 0
