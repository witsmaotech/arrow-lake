"""Coverage for BackupManager — backup create/restore/list/verify."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.config import StorageBackend, StorageConfig
from arrow_lake.ops.backup import BackupInfo, BackupManager, BackupManifest


@dataclass(frozen=True)
class _FakeListResult:
    keys: list[str]
    next_token: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class _FakeHeadResult:
    etag: str = "abc123"
    size_bytes: int = 100


@dataclass(frozen=True)
class _FakeUploadResult:
    key: str
    size_bytes: int = 100


def _make_config() -> StorageConfig:
    cfg = MagicMock(spec=StorageConfig)
    cfg.backend = StorageBackend.LOCAL
    cfg.s3_bucket = "test-bucket"
    cfg.base_uri = "/tmp/test-lake"
    cfg.s3_endpoint = "http://localhost:9000"
    cfg.s3_uri = "s3://test-bucket"
    cfg.to_storage_options.return_value = {}
    return cfg


def _make_blob_store() -> MagicMock:
    bs = MagicMock()
    bs.upload.return_value = _FakeUploadResult(key="backups/test/manifest.json")
    bs.download.return_value = b'{"backup_id":"test","created_at":"2025-01-01","datasets":[],"blob_prefixes":[],"status":"complete","total_size_bytes":0}'
    bs.list_blobs.return_value = _FakeListResult(keys=["backups/b1/manifest.json"])
    bs.head.return_value = _FakeHeadResult()
    bs.delete_prefix.return_value = 5
    return bs


@pytest.fixture
def blob_store() -> MagicMock:
    return _make_blob_store()


@pytest.fixture
def manager(blob_store: MagicMock) -> BackupManager:
    return BackupManager(
        storage_config=_make_config(),
        lance_base_uri=Path("/tmp/test-lake"),
        blob_store=blob_store,
    )


# ── BackupManifest ──


class TestBackupManifest:
    def test_to_json_roundtrip(self) -> None:
        m = BackupManifest(backup_id="b1", created_at="2025-01-01")
        raw = m.to_json()
        parsed = json.loads(raw)
        assert parsed["backup_id"] == "b1"

    def test_from_json(self) -> None:
        raw = '{"backup_id":"b1","created_at":"2025-01-01","datasets":[],"blob_prefixes":[],"status":"complete","total_size_bytes":0,"unknown_field":"ignore"}'
        m = BackupManifest.from_json(raw)
        assert m.backup_id == "b1"


# ── create_backup ──


class TestCreateBackup:
    def test_empty_backup(self, manager: BackupManager, blob_store: MagicMock) -> None:
        info = manager.create_backup(backup_id="test-empty")
        assert info.backup_id == "test-empty"
        assert info.datasets == ()
        assert info.status == "complete"
        blob_store.upload.assert_called()

    def test_backup_with_blob_prefixes(self, manager: BackupManager, blob_store: MagicMock) -> None:
        blob_store.list_blobs.return_value = _FakeListResult(keys=["uploads/ds/file1.csv"])
        info = manager.create_backup(blob_prefixes=["uploads/ds/"], backup_id="bp1")
        assert info.status == "complete"


# ── restore_backup ──


class TestRestoreBackup:
    def test_restore_success(self, manager: BackupManager) -> None:
        with patch.object(manager, "_get_restorer") as mock_get:
            mock_restorer = MagicMock()
            mock_get.return_value = mock_restorer
            info = manager.restore_backup("test")
        assert info.status == "restored"
        assert info.backup_id == "test"


# ── list_backups ──


class TestListBackups:
    def test_list_empty(self, manager: BackupManager, blob_store: MagicMock) -> None:
        blob_store.list_blobs.return_value = _FakeListResult(keys=[])
        result = manager.list_backups()
        assert result == []

    def test_list_with_backups(self, manager: BackupManager, blob_store: MagicMock) -> None:
        blob_store.list_blobs.return_value = _FakeListResult(
            keys=["backups/b1/manifest.json", "backups/b2/manifest.json"],
        )
        result = manager.list_backups()
        assert len(result) == 2


# ── get_backup_info ──


class TestGetBackupInfo:
    def test_get_info(self, manager: BackupManager) -> None:
        info = manager.get_backup_info("test")
        assert info.backup_id == "test"


# ── delete_backup ──


class TestDeleteBackup:
    def test_delete(self, manager: BackupManager, blob_store: MagicMock) -> None:
        manager.delete_backup("test")
        blob_store.delete_prefix.assert_called_once()


# ── verify_backup ──


class TestVerifyBackup:
    def test_verify_no_hashes(self, manager: BackupManager, blob_store: MagicMock) -> None:
        # Default manifest has empty datasets
        result = manager.verify_backup("test")
        assert result is True

    def test_verify_with_matching_hash(self, manager: BackupManager, blob_store: MagicMock) -> None:
        import hashlib
        content = b"test data"
        expected_hash = hashlib.sha256(content).hexdigest()
        manifest_data = {
            "backup_id": "v1",
            "created_at": "2025-01-01",
            "datasets": [{"name": "ds1", "rows": 10, "file_hashes": {"data.lance": expected_hash}}],
            "blob_prefixes": [],
            "status": "complete",
            "total_size_bytes": 100,
        }
        blob_store.download.side_effect = [
            json.dumps(manifest_data).encode(),  # manifest
            content,  # file data
        ]
        result = manager.verify_backup("v1")
        assert result is True


# ── _manifest_to_info ──


class TestManifestToInfo:
    def test_conversion(self) -> None:
        m = BackupManifest(
            backup_id="b1", created_at="2025",
            datasets=[{"name": "ds1"}],
            blob_prefixes=[{"prefix": "up/"}],
            total_size_bytes=42,
        )
        info = BackupManager._manifest_to_info(m)
        assert info.backup_id == "b1"
        assert info.datasets == ("ds1",)
        assert info.blob_prefixes == ("up/",)
        assert info.total_size_bytes == 42


# ── _generate_backup_id ──


class TestGenerateBackupId:
    def test_format(self) -> None:
        bid = BackupManager._generate_backup_id()
        assert "z" in bid
        assert len(bid) > 10


# ── BackupInfo ──


class TestBackupInfo:
    def test_frozen(self) -> None:
        info = BackupInfo(backup_id="b1", created_at="2025", datasets=(), blob_prefixes=(), total_size_bytes=0, status="ok")
        with pytest.raises(AttributeError):
            info.backup_id = "b2"  # type: ignore[misc]


# ── _load_manifest errors ──


class TestLoadManifest:
    def test_not_found_raises(self, manager: BackupManager, blob_store: MagicMock) -> None:
        from arrow_lake.exceptions import ErrorCode, StorageError
        blob_store.download.side_effect = StorageError(error_code=ErrorCode.BLOB_NOT_FOUND, message="not found")
        with pytest.raises(StorageError, match="not found"):
            manager._load_manifest("nonexistent")
