"""Tests for LanceStorageManager.scan_dataset() — streaming scanner.

TDD RED phase: these tests define the expected behavior of scan_dataset()
before the implementation exists.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.exceptions import StorageError
from arrow_lake.ingest.storage import LanceStorageManager


def _make_storage(tmp_path: Path) -> LanceStorageManager:
    """Create a LanceStorageManager with a temp directory."""
    return LanceStorageManager(str(tmp_path))


def _make_sample_table(rows: int = 100) -> pa.Table:
    """Create a sample Arrow table for testing."""
    return pa.table(
        {
            "id": [f"doc-{i}" for i in range(rows)],
            "modality": ["text" if i % 2 == 0 else "image" for i in range(rows)],
            "value": [float(i) for i in range(rows)],
        }
    )


class TestScanDatasetSignature:
    """Test scan_dataset() exists with correct signature."""

    def test_method_exists(self, tmp_path: Path) -> None:
        storage = _make_storage(tmp_path)
        assert hasattr(storage, "scan_dataset")
        assert callable(storage.scan_dataset)

    def test_returns_record_batch_reader(self, tmp_path: Path) -> None:
        storage = _make_storage(tmp_path)
        # Create a real dataset first
        table = _make_sample_table(50)
        storage.create_dataset("test_data", table)

        reader = storage.scan_dataset("test_data")
        assert isinstance(reader, pa.RecordBatchReader)

    def test_reader_schema_matches_dataset(self, tmp_path: Path) -> None:
        storage = _make_storage(tmp_path)
        table = _make_sample_table(50)
        storage.create_dataset("test_data", table)

        reader = storage.scan_dataset("test_data")
        assert reader.schema == table.schema


class TestScanDatasetValidation:
    """Test scan_dataset() validates inputs."""

    def test_invalid_name_raises(self, tmp_path: Path) -> None:
        storage = _make_storage(tmp_path)
        with pytest.raises(StorageError, match="Invalid dataset name"):
            storage.scan_dataset("../etc/passwd")

    def test_nonexistent_dataset_raises(self, tmp_path: Path) -> None:
        storage = _make_storage(tmp_path)
        with pytest.raises(StorageError, match="not found"):
            storage.scan_dataset("nonexistent")

    def test_empty_name_raises(self, tmp_path: Path) -> None:
        storage = _make_storage(tmp_path)
        with pytest.raises(StorageError, match="Invalid dataset name"):
            storage.scan_dataset("")


class TestScanDatasetStreaming:
    """Test that scan_dataset() streams without full materialization."""

    def test_reads_all_rows(self, tmp_path: Path) -> None:
        storage = _make_storage(tmp_path)
        table = _make_sample_table(200)
        storage.create_dataset("test_data", table)

        reader = storage.scan_dataset("test_data")
        result = reader.read_all()
        assert result.num_rows == 200

    def test_column_subset(self, tmp_path: Path) -> None:
        storage = _make_storage(tmp_path)
        table = _make_sample_table(100)
        storage.create_dataset("test_data", table)

        reader = storage.scan_dataset("test_data", columns=["id", "modality"])
        result = reader.read_all()
        assert result.num_rows == 100
        assert result.column_names == ["id", "modality"]

    def test_batch_size_respected(self, tmp_path: Path) -> None:
        storage = _make_storage(tmp_path)
        table = _make_sample_table(150)
        storage.create_dataset("test_data", table)

        reader = storage.scan_dataset("test_data", batch_size=50)
        batches = list(reader)
        # 150 rows / 50 per batch = 3 batches
        assert len(batches) == 3
        for batch in batches:
            assert batch.num_rows <= 50

    def test_custom_batch_size(self, tmp_path: Path) -> None:
        storage = _make_storage(tmp_path)
        table = _make_sample_table(100)
        storage.create_dataset("test_data", table)

        reader = storage.scan_dataset("test_data", batch_size=33)
        batches = list(reader)
        # 100 / 33 = 3 full batches + 1 partial
        assert len(batches) == 4  # ceil(100/33)


class TestScanDatasetWithFilter:
    """Test scan_dataset() filter pushdown."""

    def test_filter_reduces_rows(self, tmp_path: Path) -> None:
        storage = _make_storage(tmp_path)
        table = _make_sample_table(100)
        storage.create_dataset("test_data", table)

        # Lance filter expression — only even values
        reader = storage.scan_dataset("test_data", filter_expr="value % 2 = 0")
        result = reader.read_all()
        assert result.num_rows < 100


class TestScanVsReadEquivalence:
    """Test that scan_dataset() and read_dataset() return same data."""

    def test_same_data(self, tmp_path: Path) -> None:
        storage = _make_storage(tmp_path)
        table = _make_sample_table(50)
        storage.create_dataset("test_data", table)

        full = storage.read_dataset("test_data")
        streamed = storage.scan_dataset("test_data").read_all()

        assert full.num_rows == streamed.num_rows
        assert full.column_names == streamed.column_names

    def test_same_columns(self, tmp_path: Path) -> None:
        storage = _make_storage(tmp_path)
        table = _make_sample_table(50)
        storage.create_dataset("test_data", table)

        full = storage.read_dataset("test_data", columns=["id"])
        streamed = storage.scan_dataset("test_data", columns=["id"]).read_all()

        assert full.column_names == streamed.column_names == ["id"]
