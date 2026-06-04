"""Cover missing lines in arrow_lake.ops.backup."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from arrow_lake.config import StorageBackend, StorageConfig
from arrow_lake.exceptions import BackupError, ErrorCode, StorageError
from arrow_lake.ops.backup import BackupManager, BackupManifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(*, backend: StorageBackend = StorageBackend.LOCAL) -> StorageConfig:
    return StorageConfig(
        backend=backend,
        base_uri="/tmp/test_lake",
        s3_bucket="test-bucket",
        s3_endpoint="http://localhost:9000",
    )


def _mgr(**kw: object) -> BackupManager:
    blob = MagicMock()
    return BackupManager(
        storage_config=_cfg(),
        lance_base_uri="/tmp/test_lake",
        blob_store=blob,
        **kw,
    )


# ---------------------------------------------------------------------------
# blob_store property
# ---------------------------------------------------------------------------


class TestBlobStoreProperty:
    def test_blob_store(self) -> None:
        m = _mgr()
        assert m.blob_store is not None


# ---------------------------------------------------------------------------
# create_backup branches
# ---------------------------------------------------------------------------


class TestCreateBackup:
    def test_with_blob_prefix_storage_error(self) -> None:
        m = _mgr()
        m._blob_store.list_blobs.return_value = MagicMock(keys=[], truncated=False, next_token=None)
        with patch.object(m, "_backup_blob_prefix", side_effect=StorageError(error_code=ErrorCode.BLOB_UPLOAD_FAILED, message="err")), \
             patch.object(m, "_estimate_backup_size", return_value=0):
            info = m.create_backup(blob_prefixes=["data/"])
        assert info.status == "partial"

    def test_with_blob_prefix_unexpected_error(self) -> None:
        m = _mgr()
        with patch.object(m, "_backup_blob_prefix", side_effect=RuntimeError("boom")):
            with patch.object(m, "_estimate_backup_size", return_value=0):
                info = m.create_backup(blob_prefixes=["data/"])
        assert info.status == "partial"

    def test_dataset_os_error(self) -> None:
        m = _mgr()
        with patch.object(m, "_backup_lance_dataset", side_effect=OSError("io")):
            with patch.object(m, "_estimate_backup_size", return_value=0):
                info = m.create_backup(dataset_names=["ds1"])
        assert info.status == "partial"


# ---------------------------------------------------------------------------
# restore_backup branches
# ---------------------------------------------------------------------------


class TestRestoreBackup:
    def test_restore_blob_prefix_item(self) -> None:
        m = _mgr()
        manifest = BackupManifest(
            backup_id="b1",
            created_at="2024-01-01",
            blob_prefixes=[{"prefix": "blobs/"}],
        )
        with patch.object(m, "_load_manifest", return_value=manifest):
            restorer = MagicMock()
            with patch.object(m, "_get_restorer", return_value=restorer):
                info = m.restore_backup("b1", blob_prefixes=["blobs/"])
        assert info.backup_id == "b1"

    def test_restore_item_os_error_raises(self) -> None:
        m = _mgr()
        manifest = BackupManifest(backup_id="b1", created_at="2024-01-01")
        with patch.object(m, "_load_manifest", return_value=manifest):
            restorer = MagicMock()
            restorer.restore_lance_dataset.side_effect = OSError("disk")
            with patch.object(m, "_get_restorer", return_value=restorer):
                with pytest.raises(BackupError):
                    m.restore_backup("b1", dataset_names=["ds1"])


# ---------------------------------------------------------------------------
# list_backups / get_backup_info / delete / verify
# ---------------------------------------------------------------------------


class TestListBackups:
    def test_manifest_load_failure_skipped(self) -> None:
        m = _mgr()
        m._blob_store.list_blobs.return_value = MagicMock(
            keys=["backups/b1/manifest.json"],
            truncated=False,
            next_token=None,
        )
        with patch.object(m, "_load_manifest", side_effect=StorageError(error_code=ErrorCode.BLOB_DOWNLOAD_FAILED, message="err")):
            result = m.list_backups()
        assert result == []


class TestGetBackupInfo:
    def test_not_found(self) -> None:
        m = _mgr()
        m._blob_store.download.side_effect = StorageError(
            error_code=ErrorCode.BLOB_NOT_FOUND, message="not found"
        )
        with pytest.raises(StorageError):
            m.get_backup_info("missing")


class TestDeleteBackup:
    def test_delete(self) -> None:
        m = _mgr()
        m._blob_store.delete_prefix.return_value = 5
        m.delete_backup("b1")
        m._blob_store.delete_prefix.assert_called_once()

    def test_delete_logs_count(self) -> None:
        m = _mgr()
        m._blob_store.delete_prefix.return_value = 3
        m.delete_backup("b1")  # should not raise


class TestVerifyBackup:
    def test_verify_checksum_mismatch(self) -> None:
        m = _mgr()
        manifest = BackupManifest(
            backup_id="b1",
            created_at="2024-01-01",
            datasets=[{"name": "ds1", "file_hashes": {"f.lance": "badhash"}}],
        )
        with patch.object(m, "_load_manifest", return_value=manifest):
            m._blob_store.download.return_value = b"realdata"
            result = m.verify_backup("b1")
        assert result is False

    def test_verify_read_failed(self) -> None:
        m = _mgr()
        manifest = BackupManifest(
            backup_id="b1",
            created_at="2024-01-01",
            datasets=[{"name": "ds1", "file_hashes": {"f.lance": "abc"}}],
        )
        with patch.object(m, "_load_manifest", return_value=manifest):
            m._blob_store.download.side_effect = OSError("io")
            result = m.verify_backup("b1")
        assert result is False

    def test_verify_no_hashes(self) -> None:
        m = _mgr()
        manifest = BackupManifest(
            backup_id="b1",
            created_at="2024-01-01",
            datasets=[{"name": "ds1", "file_hashes": {}}],
        )
        with patch.object(m, "_load_manifest", return_value=manifest):
            assert m.verify_backup("b1") is True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestPaginateKeys:
    def test_pagination_loop(self) -> None:
        m = _mgr()
        m._blob_store.list_blobs.side_effect = [
            MagicMock(keys=["a", "b"], truncated=True, next_token="tok"),
            MagicMock(keys=["c"], truncated=False, next_token=None),
        ]
        keys = m._paginate_keys("pre/")
        assert keys == ["a", "b", "c"]


class TestBackupLanceDatasetRemote:
    def test_remote_backup(self) -> None:
        cfg = _cfg(backend=StorageBackend.S3)
        m = BackupManager(storage_config=cfg, lance_base_uri="/tmp", blob_store=MagicMock())
        s3_prefix = m._s3_dataset_prefix("ds")
        m._blob_store.list_blobs.return_value = MagicMock(
            keys=[f"{s3_prefix}data.lance"],
            truncated=False,
            next_token=None,
        )
        m._blob_store.head.return_value = MagicMock(etag="abc", size_bytes=100)
        rows, hashes = m._backup_lance_dataset_remote("ds", "backups/b1/")
        assert len(hashes) == 1

    def test_remote_not_found(self) -> None:
        cfg = _cfg(backend=StorageBackend.S3)
        m = BackupManager(storage_config=cfg, lance_base_uri="/tmp", blob_store=MagicMock())
        m._blob_store.list_blobs.return_value = MagicMock(
            keys=[], truncated=False, next_token=None,
        )
        with pytest.raises(FileNotFoundError):
            m._backup_lance_dataset_remote("ds", "backups/b1/")

    def test_remote_head_exception(self) -> None:
        cfg = _cfg(backend=StorageBackend.S3)
        m = BackupManager(storage_config=cfg, lance_base_uri="/tmp", blob_store=MagicMock())
        # Keys must start with the s3_prefix "/tmp/test_lake/ds.lance/"
        s3_prefix = m._s3_dataset_prefix("ds")
        m._blob_store.list_blobs.return_value = MagicMock(
            keys=[f"{s3_prefix}data.lance"],
            truncated=False,
            next_token=None,
        )
        m._blob_store.head.side_effect = Exception("err")
        rows, hashes = m._backup_lance_dataset_remote("ds", "backups/b1/")
        assert hashes.get("data.lance") == "copy-ok"

    def test_remote_lancedb_import_fails(self) -> None:
        cfg = _cfg(backend=StorageBackend.S3)
        m = BackupManager(storage_config=cfg, lance_base_uri="/tmp", blob_store=MagicMock())
        s3_prefix = m._s3_dataset_prefix("ds")
        m._blob_store.list_blobs.return_value = MagicMock(
            keys=[f"{s3_prefix}data.lance"],
            truncated=False,
            next_token=None,
        )
        m._blob_store.head.return_value = MagicMock(etag="x", size_bytes=1)
        with patch.dict("sys.modules", {"lancedb": None}):
            rows, hashes = m._backup_lance_dataset_remote("ds", "backups/b1/")
        assert rows == -1

    def test_remote_pagination(self) -> None:
        cfg = _cfg(backend=StorageBackend.S3)
        m = BackupManager(storage_config=cfg, lance_base_uri="/tmp", blob_store=MagicMock())
        m._blob_store.list_blobs.side_effect = [
            MagicMock(keys=["k1"], truncated=True, next_token="tok"),
            MagicMock(keys=[], truncated=False, next_token=None),
        ]
        m._blob_store.head.return_value = MagicMock(etag="a", size_bytes=1)
        with patch.dict("sys.modules", {"lancedb": None}):
            rows, hashes = m._backup_lance_dataset_remote("ds", "backups/b1/")
        assert rows == -1


class TestS3DatasetPrefix:
    def test_with_dot_slash(self) -> None:
        cfg = StorageConfig(
            backend=StorageBackend.S3,
            base_uri="./data",
            s3_bucket="test-bucket",
            s3_endpoint="http://localhost:9000",
        )
        m = BackupManager(storage_config=cfg, lance_base_uri="/tmp", blob_store=MagicMock())
        result = m._s3_dataset_prefix("ds")
        assert result == "data/ds.lance/"


class TestBackupBlobPrefix:
    def test_basic(self) -> None:
        m = _mgr()
        m._blob_store.list_blobs.return_value = MagicMock(
            keys=["data/f1"], truncated=False, next_token=None,
        )
        count = m._backup_blob_prefix("data/", "backups/b1/blobs/")
        assert count == 1

    def test_pagination(self) -> None:
        m = _mgr()
        m._blob_store.list_blobs.side_effect = [
            MagicMock(keys=["k1"], truncated=True, next_token="tok"),
            MagicMock(keys=["k2"], truncated=False, next_token=None),
        ]
        count = m._backup_blob_prefix("data/", "dest/")
        assert count == 2


class TestLoadManifest:
    def test_not_found(self) -> None:
        m = _mgr()
        m._blob_store.download.side_effect = StorageError(
            error_code=ErrorCode.BLOB_NOT_FOUND, message="nf"
        )
        with pytest.raises(StorageError, match="not found"):
            m._load_manifest("missing")

    def test_other_storage_error_reraise(self) -> None:
        m = _mgr()
        m._blob_store.download.side_effect = StorageError(
            error_code=ErrorCode.STORAGE_READ_FAILED, message="err"
        )
        with pytest.raises(StorageError):
            m._load_manifest("x")


class TestEstimateBackupSize:
    def test_basic(self) -> None:
        m = _mgr()
        m._blob_store.list_blobs.return_value = MagicMock(
            keys=["k1"], truncated=False, next_token=None,
        )
        m._blob_store.head.return_value = MagicMock(size_bytes=100)
        assert m._estimate_backup_size("pre/") == 100

    def test_head_error_skipped(self) -> None:
        m = _mgr()
        m._blob_store.list_blobs.return_value = MagicMock(
            keys=["k1"], truncated=False, next_token=None,
        )
        m._blob_store.head.side_effect = StorageError(error_code=ErrorCode.BLOB_NOT_FOUND, message="err")
        assert m._estimate_backup_size("pre/") == 0


class TestGenerateBackupId:
    def test_format(self) -> None:
        bid = BackupManager._generate_backup_id()
        assert "z" in bid
        assert len(bid) > 10


# ---------------------------------------------------------------------------
# BackupManifest serialization
# ---------------------------------------------------------------------------


class TestBackupManifest:
    def test_roundtrip(self) -> None:
        m = BackupManifest(backup_id="b1", created_at="2024-01-01")
        raw = m.to_json()
        restored = BackupManifest.from_json(raw)
        assert restored.backup_id == "b1"

    def test_from_json_ignores_extra(self) -> None:
        raw = json.dumps({"backup_id": "b1", "created_at": "2024", "extra": True})
        m = BackupManifest.from_json(raw)
        assert m.backup_id == "b1"
