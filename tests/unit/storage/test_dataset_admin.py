"""Tests for rename_dataset, copy_dataset, merge_datasets on LanceStorageManager."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest
import pyarrow as pa

from arrow_lake.exceptions import ErrorCode, StorageError
from arrow_lake.ingest.storage import LanceStorageManager


@pytest.fixture
def mgr(tmp_path):
    return LanceStorageManager(tmp_path)


class TestRenameDataset:
    def test_rename_calls_read_create_delete(self, mgr):
        """rename_dataset reads source, creates target, deletes source."""
        data = pa.table({"id": [1]})
        with patch.object(mgr, "dataset_exists", side_effect=lambda n: n == "old"):
            with patch.object(mgr, "read_dataset", return_value=data) as mock_read:
                with patch.object(mgr, "create_dataset") as mock_create:
                    with patch.object(mgr, "delete_dataset") as mock_delete:
                        mgr.rename_dataset("old", "new")
                        mock_read.assert_called_once_with("old")
                        mock_create.assert_called_once_with("new", data)
                        mock_delete.assert_called_once_with("old")

    def test_rename_source_not_found(self, mgr):
        """Raises StorageError if source doesn't exist."""
        with patch.object(mgr, "dataset_exists", return_value=False):
            with pytest.raises(StorageError, match="not found"):
                mgr.rename_dataset("missing", "new")

    def test_rename_target_exists(self, mgr):
        """Raises StorageError if target already exists."""
        with patch.object(mgr, "dataset_exists", return_value=True):
            with pytest.raises(StorageError, match="already exists"):
                mgr.rename_dataset("old", "existing")


class TestCopyDataset:
    def test_copy_calls_read_and_create(self, mgr):
        """copy_dataset reads source and creates target."""
        data = pa.table({"id": [1]})
        with patch.object(mgr, "dataset_exists", side_effect=lambda n: n == "src"):
            with patch.object(mgr, "read_dataset", return_value=data) as mock_read:
                with patch.object(mgr, "create_dataset") as mock_create:
                    mgr.copy_dataset("src", "dst")
                    mock_read.assert_called_once_with("src")
                    mock_create.assert_called_once_with("dst", data)

    def test_copy_source_not_found(self, mgr):
        """Raises StorageError if source doesn't exist."""
        with patch.object(mgr, "dataset_exists", return_value=False):
            with pytest.raises(StorageError, match="not found"):
                mgr.copy_dataset("missing", "dst")

    def test_copy_target_exists(self, mgr):
        """Raises StorageError if target already exists."""
        with patch.object(mgr, "dataset_exists", return_value=True):
            with pytest.raises(StorageError, match="already exists"):
                mgr.copy_dataset("src", "existing")


class TestMergeDatasets:
    def test_merge_concat_and_create(self, mgr):
        """merge_datasets reads all sources and creates target."""
        data = pa.table({"id": [1]})
        with patch.object(mgr, "dataset_exists", side_effect=lambda n: n != "target"):
            with patch.object(mgr, "read_dataset", return_value=data) as mock_read:
                with patch.object(mgr, "create_dataset") as mock_create:
                    mgr.merge_datasets(["a", "b"], "target")
                    assert mock_read.call_count == 2
                    mock_create.assert_called_once()

    def test_merge_schema_mismatch(self, mgr):
        """Raises StorageError when schemas don't match."""
        data1 = pa.table({"id": pa.array([1], type=pa.int32())})
        data2 = pa.table({"id": pa.array([1], type=pa.int64())})
        with patch.object(mgr, "dataset_exists", side_effect=lambda n: n != "target"):
            with patch.object(mgr, "read_dataset", side_effect=[data1, data2]):
                with pytest.raises(StorageError, match="Schema mismatch"):
                    mgr.merge_datasets(["a", "b"], "target")

    def test_merge_empty_sources(self, mgr):
        """Raises StorageError for empty source list."""
        with pytest.raises(StorageError, match="must not be empty"):
            mgr.merge_datasets([], "target")

    def test_merge_source_not_found(self, mgr):
        """Raises StorageError if any source doesn't exist."""
        with patch.object(mgr, "dataset_exists", return_value=False):
            with pytest.raises(StorageError, match="not found"):
                mgr.merge_datasets(["missing"], "target")

    def test_merge_target_exists(self, mgr):
        """Raises StorageError if target already exists."""
        with patch.object(mgr, "dataset_exists", return_value=True):
            with pytest.raises(StorageError, match="already exists"):
                mgr.merge_datasets(["a"], "target")
