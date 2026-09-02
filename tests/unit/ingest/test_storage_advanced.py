"""Tests for StorageAdvancedMixin: compaction, schema migration, scan, copy/rename/merge."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

import pyarrow as pa
import pytest

from arrow_lake.exceptions import ErrorCode, StorageError
from arrow_lake.ingest._storage_advanced import StorageAdvancedMixin


def _make_table(**columns: list) -> pa.Table:
    return pa.table(columns)


def _make_mixin(tmp_path) -> StorageAdvancedMixin:
    """Create a StorageAdvancedMixin instance with mocked internals."""

    class _Testable(StorageAdvancedMixin):
        def __init__(self):
            self._storage_config = None
            self._storage_options = None
            self._olap_config = None
            self._base_path = tmp_path
            self._locks: dict = {}

        def _validate_name(self, name: str) -> None:
            if not name or ".." in name or "/" in name:
                raise StorageError(
                    error_code=ErrorCode.VALIDATION_MISSING_FIELD,
                    message=f"Invalid dataset name: {name!r}",
                )

        def _validate_identifier(self, ident: str, context: str = "") -> None:
            if not ident or ".." in ident:
                raise StorageError(
                    error_code=ErrorCode.VALIDATION_MISSING_FIELD,
                    message=f"Invalid identifier: {ident!r}",
                )

        def _validate_sql_expr(self, expr: str) -> None:
            if not expr:
                raise StorageError(
                    error_code=ErrorCode.VALIDATION_MISSING_FIELD,
                    message="SQL expression cannot be empty",
                )

        def _dataset_lock(self, name: str):
            from contextlib import contextmanager

            @contextmanager
            def _lock():
                yield

            return _lock()

        def _lance_dir(self, name: str, table: str | None = None):
            return self._base_path / name

        def dataset_uri(self, name: str, table: str | None = None) -> str:
            return str(self._base_path / name)

        def _get_dataset_path(self, name: str) -> str:
            return str(self._base_path / name)

        def _open_lance(self, path: str) -> MagicMock:
            ds = MagicMock()
            ds.version = 1
            ds.schema = pa.schema([pa.field("name", pa.string()), pa.field("age", pa.int64())])
            return ds

        def dataset_exists(self, name: str) -> bool:
            return (self._base_path / name).exists()

        def read_dataset(self, name: str) -> pa.Table:
            return _make_table(name=["Alice", "Bob"], age=[30, 25])

        def create_dataset(self, name: str, data: pa.Table) -> None:
            (self._base_path / name).mkdir(parents=True, exist_ok=True)

        def delete_dataset(self, name: str) -> None:
            import shutil
            p = self._base_path / name
            if p.exists():
                shutil.rmtree(p)

    return _Testable()


# ---------------------------------------------------------------------------
# add_column
# ---------------------------------------------------------------------------

class TestAddColumn:
    def test_add_new_column(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "ds1").mkdir()
        mixin.add_column("ds1", "score", "CAST(0 AS DOUBLE)")

    def test_add_column_empty_sql_rejected(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "ds1").mkdir()
        with pytest.raises(StorageError):
            mixin.add_column("ds1", "score", "")

    def test_add_column_invalid_name_rejected(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        with pytest.raises(StorageError):
            mixin.add_column("..bad", "score", "CAST(0 AS DOUBLE)")

    def test_add_existing_column_rejected(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "ds1").mkdir()
        with pytest.raises(Exception):  # SchemaMigrationError
            mixin.add_column("ds1", "name", "CAST('' AS STRING)")


# ---------------------------------------------------------------------------
# alter_column
# ---------------------------------------------------------------------------

class TestAlterColumn:
    def test_alter_column_same_type(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "ds1").mkdir()
        # same type → always safe
        mixin.alter_column("ds1", "age", pa.int64())

    def test_alter_column_widening(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "ds1").mkdir()
        # int64 → float64 is a safe widening
        mixin.alter_column("ds1", "age", pa.float64())

    def test_alter_column_invalid_name_rejected(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        with pytest.raises(StorageError):
            mixin.alter_column("..bad", "age", pa.int32())


# ---------------------------------------------------------------------------
# drop_column
# ---------------------------------------------------------------------------

class TestDropColumn:
    def test_drop_column_success(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "ds1").mkdir()
        mixin.drop_column("ds1", "age")

    def test_drop_column_invalid_name(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        with pytest.raises(StorageError):
            mixin.drop_column("..bad", "age")

    def test_drop_column_nonexistent_in_schema(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "ds1").mkdir()
        # Column "nonexistent" is not in the schema → SchemaMigrationError
        with pytest.raises(Exception):  # SchemaMigrationError
            mixin.drop_column("ds1", "nonexistent")


# ---------------------------------------------------------------------------
# scan_dataset
# ---------------------------------------------------------------------------

class TestScanDataset:
    def test_scan_dataset_not_found(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        with pytest.raises(StorageError) as exc_info:
            mixin.scan_dataset("nonexistent_ds")
        assert "not found" in str(exc_info.value).lower()

    def test_scan_dataset_with_columns_filter(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "ds1").mkdir()

        mock_scanner = MagicMock()
        mock_reader = MagicMock()
        mock_scanner.to_reader.return_value = mock_reader

        mock_ds = MagicMock()
        mock_ds.scanner.return_value = mock_scanner

        with patch.dict("sys.modules", {"lance": MagicMock(dataset=MagicMock(return_value=mock_ds))}):
            import importlib
            import arrow_lake.ingest._storage_advanced as mod
            # The import is local inside scan_dataset, so patch via sys.modules
            import sys
            fake_lance = MagicMock()
            fake_lance.dataset.return_value = mock_ds
            sys.modules["lance"] = fake_lance
            result = mixin.scan_dataset("ds1", columns=["name"])
            assert result is mock_reader
            mock_ds.scanner.assert_called_once_with(
                columns=["name"], filter=None, batch_size=10_000
            )

    def test_scan_dataset_with_filter(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "ds1").mkdir()

        mock_scanner = MagicMock()
        mock_reader = MagicMock()
        mock_scanner.to_reader.return_value = mock_reader

        mock_ds = MagicMock()
        mock_ds.scanner.return_value = mock_scanner

        fake_lance = MagicMock()
        fake_lance.dataset.return_value = mock_ds
        with patch.dict("sys.modules", {"lance": fake_lance}):
            result = mixin.scan_dataset("ds1", filter_expr="age > 20", batch_size=5000)
            assert result is mock_reader
            mock_ds.scanner.assert_called_once_with(
                columns=None, filter="age > 20", batch_size=5000
            )


# ---------------------------------------------------------------------------
# rename_dataset
# ---------------------------------------------------------------------------

class TestRenameDataset:
    def test_rename_success(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "old_name").mkdir()
        mixin.rename_dataset("old_name", "new_name")
        assert (tmp_path / "new_name").exists()
        assert not (tmp_path / "old_name").exists()

    def test_rename_source_not_found(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        with pytest.raises(StorageError) as exc_info:
            mixin.rename_dataset("nonexistent", "new_name")
        assert "not found" in str(exc_info.value).lower()

    def test_rename_target_exists(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "dst").mkdir()
        with pytest.raises(StorageError) as exc_info:
            mixin.rename_dataset("src", "dst")
        assert "already exists" in str(exc_info.value).lower()

    def test_rename_invalid_source_name(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        with pytest.raises(StorageError):
            mixin.rename_dataset("..bad", "new_name")


# ---------------------------------------------------------------------------
# copy_dataset
# ---------------------------------------------------------------------------

class TestCopyDataset:
    def test_copy_success(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "src").mkdir()
        mixin.copy_dataset("src", "copy")
        assert (tmp_path / "src").exists()
        assert (tmp_path / "copy").exists()

    def test_copy_source_not_found(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        with pytest.raises(StorageError) as exc_info:
            mixin.copy_dataset("nonexistent", "copy")
        assert "not found" in str(exc_info.value).lower()

    def test_copy_target_exists(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "existing").mkdir()
        with pytest.raises(StorageError) as exc_info:
            mixin.copy_dataset("src", "existing")
        assert "already exists" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# merge_datasets
# ---------------------------------------------------------------------------

class TestMergeDatasets:
    def test_merge_success(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        mixin.merge_datasets(["a", "b"], "merged")
        assert (tmp_path / "merged").exists()

    def test_merge_empty_sources_rejected(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        with pytest.raises(StorageError) as exc_info:
            mixin.merge_datasets([], "merged")
        assert "must not be empty" in str(exc_info.value)

    def test_merge_source_not_found(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "a").mkdir()
        with pytest.raises(StorageError) as exc_info:
            mixin.merge_datasets(["a", "nonexistent"], "merged")
        assert "not found" in str(exc_info.value).lower()

    def test_merge_target_exists(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "a").mkdir()
        (tmp_path / "existing").mkdir()
        with pytest.raises(StorageError) as exc_info:
            mixin.merge_datasets(["a"], "existing")
        assert "already exists" in str(exc_info.value).lower()

    def test_merge_schema_mismatch(self, tmp_path) -> None:
        mixin = _make_mixin(tmp_path)
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()

        call_count = [0]
        original_read = mixin.read_dataset

        def _read(name):
            call_count[0] += 1
            if name == "a":
                return _make_table(name=["Alice"], age=[30])
            return _make_table(name=["Bob"], score=[100.0])

        mixin.read_dataset = _read
        with pytest.raises(StorageError) as exc_info:
            mixin.merge_datasets(["a", "b"], "merged")
        assert "Schema mismatch" in str(exc_info.value)
