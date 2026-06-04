"""Cover missing lines in arrow_lake.ingest.storage."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pyarrow as pa
import pytest

from arrow_lake.config import StorageBackend, StorageConfig
from arrow_lake.exceptions import ErrorCode, StorageError
from arrow_lake.ingest.storage import LanceStorageManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(*, backend: StorageBackend = StorageBackend.LOCAL, **kw: object) -> StorageConfig:
    return StorageConfig(
        backend=backend,
        base_uri="/tmp/test_lake",
        s3_bucket="test-bucket",
        s3_endpoint="http://localhost:9000",
        **kw,
    )


def _mgr(**kw: object) -> LanceStorageManager:
    cfg = _cfg()
    return LanceStorageManager(base_uri=cfg.base_uri, storage_config=cfg, **kw)


# ---------------------------------------------------------------------------
# __init__ / storage_options / _get_io_config
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_init(self) -> None:
        m = _mgr()
        assert m is not None

    def test_storage_options_s3(self) -> None:
        cfg = _cfg(backend=StorageBackend.S3)
        m = LanceStorageManager(base_uri=cfg.base_uri, storage_config=cfg)
        opts = m.storage_options
        assert isinstance(opts, dict)

    def test_storage_options_local_none(self) -> None:
        m = _mgr()
        assert m.storage_options is None

    def test_get_io_config_s3(self) -> None:
        cfg = _cfg(backend=StorageBackend.S3)
        m = LanceStorageManager(base_uri=cfg.base_uri, storage_config=cfg)
        with patch("daft.IOConfig") as mock_io, \
             patch("daft.daft.S3Config") as mock_s3:
            mock_io.return_value = MagicMock()
            result = m._get_io_config()
            assert result is not None

    def test_get_io_config_local(self) -> None:
        m = _mgr()
        # Local storage has no storage_options → returns None
        result = m._get_io_config()
        assert result is None


# ---------------------------------------------------------------------------
# write_lance_from_dataframe
# ---------------------------------------------------------------------------


class TestWriteLanceFromDataframe:
    def test_error_path(self) -> None:
        m = _mgr()
        mock_df = MagicMock()
        mock_df.write_lance.side_effect = RuntimeError("write fail")
        with pytest.raises(StorageError):
            m.write_lance_from_dataframe("test_ds", mock_df)


# ---------------------------------------------------------------------------
# dataset_uri
# ---------------------------------------------------------------------------


class TestDatasetUri:
    def test_s3_path(self) -> None:
        cfg = _cfg(backend=StorageBackend.S3)
        with patch.object(LanceStorageManager, "__init__", lambda self, *a, **k: None):
            m = LanceStorageManager.__new__(LanceStorageManager)
            m._storage_config = cfg
            result = m.dataset_uri("myds")
            assert "myds" in result


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class TestValidation:
    def test_validate_identifier(self) -> None:
        with pytest.raises(StorageError):
            LanceStorageManager._validate_identifier("bad name!", "column")

    def test_validate_sql_expr_dangerous(self) -> None:
        with pytest.raises(StorageError):
            LanceStorageManager._validate_sql_expr("DROP TABLE x")

    def test_validate_sql_expr_semicolon(self) -> None:
        with pytest.raises(StorageError):
            LanceStorageManager._validate_sql_expr("a; b")


# ---------------------------------------------------------------------------
# _write_lance branches
# ---------------------------------------------------------------------------


class TestWriteLance:
    def test_create_with_optimize(self) -> None:
        m = _mgr()
        mock_db = MagicMock()
        mock_table = MagicMock()
        mock_db.open_table.return_value = mock_table
        m._db = mock_db
        m._storage_config = _cfg()
        m._storage_config._lance_max_rows_per_file = 10000
        tbl = pa.table({"a": [1]})
        m._write_lance(tbl, "/tmp/test_ds", mode="create")
        mock_db.create_table.assert_called_once()

    def test_create_optimize_error(self) -> None:
        m = _mgr()
        mock_db = MagicMock()
        mock_table = MagicMock()
        mock_table.optimize.side_effect = RuntimeError("err")
        mock_db.open_table.return_value = mock_table
        m._db = mock_db
        m._storage_config = _cfg()
        m._storage_config._lance_max_rows_per_file = 10000
        tbl = pa.table({"a": [1]})
        # should not raise, just debug log
        m._write_lance(tbl, "/tmp/test_ds", mode="create")

    def test_append_mode(self) -> None:
        m = _mgr()
        mock_db = MagicMock()
        mock_table = MagicMock()
        mock_db.open_table.return_value = mock_table
        m._db = mock_db
        tbl = pa.table({"a": [1]})
        m._write_lance(tbl, "/tmp/test_ds", mode="append")
        mock_table.add.assert_called_once()


# ---------------------------------------------------------------------------
# open_dataset_versioned
# ---------------------------------------------------------------------------


class TestOpenDatasetVersioned:
    def test_local_path(self) -> None:
        m = _mgr()
        mock_lance = MagicMock()
        mock_ds = MagicMock()
        mock_lance.dataset.return_value = mock_ds
        with patch.dict("sys.modules", {"lance": mock_lance}):
            result = m.open_dataset_versioned("myds", version=1)
            assert result is mock_ds

    def test_s3_path(self) -> None:
        cfg = _cfg(backend=StorageBackend.S3)
        with patch.object(LanceStorageManager, "__init__", lambda self, *a, **k: None):
            m = LanceStorageManager.__new__(LanceStorageManager)
            m._storage_config = cfg
            m._storage_options = cfg.to_storage_options()
            m._db = None
            mock_lance = MagicMock()
            mock_ds = MagicMock()
            mock_lance.dataset.return_value = mock_ds
            with patch.dict("sys.modules", {"lance": mock_lance}):
                result = m.open_dataset_versioned("myds", version=2)
                assert result is mock_ds


# ---------------------------------------------------------------------------
# cleanup_versions
# ---------------------------------------------------------------------------


class TestCleanupVersions:
    def test_local_path_no_files(self) -> None:
        m = _mgr()
        m._storage_config = _cfg()
        mock_lance = MagicMock()
        mock_lance.dataset.side_effect = OSError("not found")
        with patch.dict("sys.modules", {"lance": mock_lance}):
            result = m.cleanup_versions("ds", older_than=timedelta(days=30))
        assert isinstance(result, int)
        assert result == 0

    def test_cleanup_with_versions(self) -> None:
        m = _mgr()
        m._storage_config = _cfg()
        mock_lance = MagicMock()
        mock_ds = MagicMock()
        mock_ds.cleanup_old_versions.return_value = MagicMock(fragments_removed=["a", "b"])
        mock_lance.dataset.return_value = mock_ds
        with patch.dict("sys.modules", {"lance": mock_lance}):
            result = m.cleanup_versions("ds", older_than=timedelta(days=30))
        assert isinstance(result, int)
        assert result >= 1

    def test_cleanup_s3(self) -> None:
        cfg = _cfg(backend=StorageBackend.S3)
        with patch.object(LanceStorageManager, "__init__", lambda self, *a, **k: None):
            m = LanceStorageManager.__new__(LanceStorageManager)
            m._storage_config = cfg
            m._storage_options = {}
            m._db = None
            mock_lance = MagicMock()
            mock_ds = MagicMock()
            mock_ds.cleanup_old_versions.return_value = MagicMock(fragments_removed=["a"])
            mock_lance.dataset.return_value = mock_ds
            with patch.object(m, "dataset_uri", return_value="s3://bucket/ds.lance"):
                with patch.dict("sys.modules", {"lance": mock_lance}):
                    result = m.cleanup_versions("ds", older_than=timedelta(days=30))
            assert isinstance(result, int)
            assert result >= 1
