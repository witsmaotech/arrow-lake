"""Tests for _LakeAdminMixin facade methods — schema evolution, versioning, I/O."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

pytest.importorskip("lance", reason="lance not installed")
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageBackend


@pytest.fixture()
def lake(tmp_path: Path) -> Lake:
    config = ArrowLakeConfig()
    config.storage.backend = StorageBackend.LOCAL
    return Lake(base_uri=str(tmp_path / "lance_data"), config=config)


@pytest.fixture()
def populated_lake(lake: Lake) -> Lake:
    table = pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"], "score": [1.0, 2.0, 3.0]})
    lake.create_dataset("test_ds", table)
    return lake


class TestRestoreDataset:
    def test_restore_dataset_replaces_data(self, lake: Lake, populated_lake: Lake) -> None:
        new_table = pa.table({"id": [10, 20], "name": ["x", "y"], "score": [10.0, 20.0]})
        lake.restore_dataset("test_ds", new_table)
        result = lake.catalog()
        entry = next(e for e in result.datasets if e.name == "test_ds")
        assert entry.num_rows == 2


class TestGetDatasetVersion:
    def test_version_after_create(self, populated_lake: Lake) -> None:
        version = populated_lake.get_dataset_version("test_ds")
        assert isinstance(version, int)
        assert version >= 0

    def test_version_after_append(self, populated_lake: Lake) -> None:
        more = pa.table({"id": [4], "name": ["d"], "score": [4.0]})
        populated_lake.append_dataset("test_ds", more)
        version = populated_lake.get_dataset_version("test_ds")
        assert version >= 1


class TestListDatasetVersions:
    def test_list_versions_returns_list(self, populated_lake: Lake) -> None:
        versions = populated_lake.list_dataset_versions("test_ds")
        assert isinstance(versions, list)
        assert len(versions) >= 1

    def test_list_versions_after_append(self, populated_lake: Lake) -> None:
        more = pa.table({"id": [4], "name": ["d"], "score": [4.0]})
        populated_lake.append_dataset("test_ds", more)
        versions = populated_lake.list_dataset_versions("test_ds")
        assert len(versions) >= 2


class TestAddColumn:
    def test_add_column(self, populated_lake: Lake) -> None:
        populated_lake.add_column("test_ds", "double_score", "score * 2")
        result = populated_lake.read_dataset("test_ds")
        assert "double_score" in result.column_names


class TestAlterColumn:
    def test_alter_column_type(self, populated_lake: Lake) -> None:
        populated_lake.alter_column("test_ds", "score", pa.float32())
        result = populated_lake.read_dataset("test_ds", columns=["score"])
        assert result.schema.field("score").type == pa.float32()


class TestDropColumn:
    def test_drop_column(self, populated_lake: Lake) -> None:
        populated_lake.drop_column("test_ds", "score")
        result = populated_lake.read_dataset("test_ds")
        assert "score" not in result.column_names
        assert "id" in result.column_names


class TestCompactDataset:
    def test_compact_runs(self, populated_lake: Lake) -> None:
        more = pa.table({"id": [4], "name": ["d"], "score": [4.0]})
        populated_lake.append_dataset("test_ds", more)
        stats = populated_lake.compact_dataset("test_ds")
        assert stats is not None


class TestReadDataset:
    def test_read_all_columns(self, populated_lake: Lake) -> None:
        result = populated_lake.read_dataset("test_ds")
        assert isinstance(result, pa.Table)
        assert result.num_rows == 3
        assert len(result.column_names) == 3

    def test_read_selected_columns(self, populated_lake: Lake) -> None:
        result = populated_lake.read_dataset("test_ds", columns=["id", "name"])
        assert result.num_rows == 3
        assert len(result.column_names) == 2


class TestScanDataset:
    def test_scan_returns_scanner(self, populated_lake: Lake) -> None:
        scanner = populated_lake.scan_dataset("test_ds")
        assert scanner is not None

    def test_scan_with_columns(self, populated_lake: Lake) -> None:
        scanner = populated_lake.scan_dataset("test_ds", columns=["id"])
        assert scanner is not None


class TestOpenDataset:
    def test_open_returns_dataset(self, populated_lake: Lake) -> None:
        ds = populated_lake.open_dataset("test_ds")
        assert ds is not None
        assert ds.count_rows() == 3


class TestRenameDataset:
    def test_rename_dataset(self, populated_lake: Lake) -> None:
        populated_lake.rename_dataset("test_ds", "renamed_ds")
        assert "renamed_ds" in populated_lake.list_datasets()
        assert "test_ds" not in populated_lake.list_datasets()

    def test_rename_nonexistent_raises(self, lake: Lake) -> None:
        from arrow_lake.exceptions import StorageError

        with pytest.raises(StorageError):
            lake.rename_dataset("nonexistent", "target")


class TestCopyDataset:
    def test_copy_dataset(self, populated_lake: Lake) -> None:
        populated_lake.copy_dataset("test_ds", "copied_ds")
        assert "copied_ds" in populated_lake.list_datasets()
        catalog = populated_lake.catalog()
        entry = next(e for e in catalog.datasets if e.name == "copied_ds")
        assert entry.num_rows == 3


class TestMergeDatasets:
    def test_merge_datasets(self, lake: Lake) -> None:
        table = pa.table({"id": [1], "val": [10]})
        lake.create_dataset("src1", table)
        lake.create_dataset("src2", table)
        lake.merge_datasets(["src1", "src2"], "merged")
        assert "merged" in lake.list_datasets()
        catalog = lake.catalog()
        entry = next(e for e in catalog.datasets if e.name == "merged")
        assert entry.num_rows == 2


class TestVersion:
    def test_version_returns_string(self, lake: Lake) -> None:
        v = lake.version()
        assert isinstance(v, str)
        assert len(v) > 0


class TestShutdown:
    def test_shutdown_no_error(self, lake: Lake) -> None:
        lake.shutdown()

    def test_shutdown_idempotent(self, lake: Lake) -> None:
        lake.shutdown()
        lake.shutdown()
