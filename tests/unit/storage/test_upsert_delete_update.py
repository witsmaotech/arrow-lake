"""Tests for upsert, delete_rows, update_rows on LanceStorageManager."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest
import pyarrow as pa

from arrow_lake.exceptions import ErrorCode, StorageError
from arrow_lake.ingest.storage import LanceStorageManager


@pytest.fixture
def mgr(tmp_path):
    return LanceStorageManager(tmp_path)


class TestUpsertDataset:
    def test_creates_if_missing(self, mgr, tmp_path):
        """When dataset doesn't exist, calls create_dataset."""
        data = pa.table({"id": [1], "val": ["a"]})
        with patch.object(mgr, "create_dataset") as mock_create, \
             patch.object(mgr, "dataset_exists", return_value=False):
            mgr.upsert_dataset("ds1", data, on="id")
            mock_create.assert_called_once_with("ds1", data)

    def test_merge_insert_called(self, mgr):
        """When dataset exists, calls table.merge_insert."""
        data = pa.table({"id": [1], "val": ["a"]})
        mock_table = MagicMock()
        mock_table.schema.names = ["id", "val"]
        mock_merge = MagicMock()
        mock_table.merge_insert.return_value = mock_merge
        mock_merge.when_matched_update_all.return_value = mock_merge
        mock_merge.when_not_matched_insert_all.return_value = mock_merge
        mock_merge.execute.return_value = None

        with patch.object(mgr, "dataset_exists", return_value=True), \
             patch.object(mgr, "_open_lance", return_value=mock_table):
            mgr.upsert_dataset("ds1", data, on="id")
            mock_table.merge_insert.assert_called_once_with(on="id")

    def test_invalid_on_column(self, mgr):
        """Raises StorageError when on-column not in schema."""
        data = pa.table({"id": [1], "val": ["a"]})
        mock_table = MagicMock()
        mock_table.schema.names = ["id", "val"]

        with patch.object(mgr, "dataset_exists", return_value=True), \
             patch.object(mgr, "_open_lance", return_value=mock_table):
            with pytest.raises(StorageError, match="not found"):
                mgr.upsert_dataset("ds1", data, on="nonexistent")

    def test_invalid_name(self, mgr):
        """Raises StorageError for invalid dataset name."""
        data = pa.table({"id": [1]})
        with pytest.raises(StorageError):
            mgr.upsert_dataset("bad name!", data)


class TestDeleteRows:
    def test_delete_calls_table_delete(self, mgr):
        """Verifies table.delete(where=...) is called."""
        mock_table = MagicMock()
        mock_table.count_rows.side_effect = [10, 7]  # before, after

        with patch.object(mgr, "_open_lance", return_value=mock_table):
            count = mgr.delete_rows("ds1", "id > 5")
            assert count == 3
            mock_table.delete.assert_called_once_with(where="id > 5")

    def test_validates_where_expression(self, mgr):
        """Dangerous SQL keywords raise StorageError."""
        with pytest.raises(StorageError, match="Dangerous"):
            mgr.delete_rows("ds1", "DROP TABLE ds1")


class TestUpdateRows:
    def test_update_calls_table_update(self, mgr):
        """Verifies table.update(where=..., values=...) is called."""
        mock_table = MagicMock()
        mock_table.schema.names = ["id", "status"]

        with patch.object(mgr, "_open_lance", return_value=mock_table):
            mgr.update_rows("ds1", "id = 1", {"status": "'active'"})
            mock_table.update.assert_called_once_with(
                where="id = 1",
                values={"status": "'active'"},
            )

    def test_missing_column(self, mgr):
        """Raises StorageError when update column not in schema."""
        mock_table = MagicMock()
        mock_table.schema.names = ["id"]

        with patch.object(mgr, "_open_lance", return_value=mock_table):
            with pytest.raises(StorageError, match="not found"):
                mgr.update_rows("ds1", "id = 1", {"nonexistent": "'x'"})

    def test_validates_where_expression(self, mgr):
        """Dangerous SQL keywords raise StorageError."""
        with pytest.raises(StorageError, match="Dangerous"):
            mgr.update_rows("ds1", "DELETE FROM ds1", {"val": "'x'"})
