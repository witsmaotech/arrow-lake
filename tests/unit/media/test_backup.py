"""Tests for BackupManager — Story M1."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.config import StorageBackend, StorageConfig
from arrow_lake.exceptions import ErrorCode, StorageError
from arrow_lake.ops.backup import (
    BackupManager,
    BackupManifest,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config() -> StorageConfig:
    return StorageConfig(
        backend=StorageBackend.LOCAL,
        base_uri="./data",
    )


def _make_blob_store() -> MagicMock:
    return MagicMock()


def _make_manifest(**overrides) -> BackupManifest:
    defaults = {
        "backup_id": "20260101T000000Zdeadbeef",
        "created_at": "2026-01-01T00:00:00+00:00",
        "datasets": [{"name": "test_ds", "rows": 100}],
        "blob_prefixes": [],
        "lance_base_uri": "./data",
        "total_size_bytes": 1024,
        "status": "complete",
    }
    defaults.update(overrides)
    return BackupManifest(**defaults)


# ---------------------------------------------------------------------------
# BackupManifest tests
# ---------------------------------------------------------------------------


class TestBackupManifest:
    def test_to_json_roundtrip(self) -> None:
        m = _make_manifest()
        json_str = m.to_json()
        parsed = BackupManifest.from_json(json_str)
        assert parsed.backup_id == m.backup_id
        assert parsed.created_at == m.created_at
        assert parsed.datasets == m.datasets
        assert parsed.status == "complete"

    def test_from_json_extra_keys_ignored(self) -> None:
        raw = json.dumps(
            {
                "backup_id": "x",
                "created_at": "2026-01-01T00:00:00+00:00",
                "unknown_field": "ignored",
            }
        )
        m = BackupManifest.from_json(raw)
        assert m.backup_id == "x"


# ---------------------------------------------------------------------------
# BackupManager.create_backup
# ---------------------------------------------------------------------------


class TestCreateBackup:
    def test_create_backup_with_datasets(self, tmp_path) -> None:
        blob_store = _make_blob_store()
        blob_store.upload.return_value = MagicMock(etag="e", size_bytes=0)
        blob_store.list_blobs.return_value = MagicMock(keys=(), count=0, truncated=False)

        ds_path = tmp_path / "data" / "my_dataset"
        ds_path.mkdir(parents=True)
        (ds_path / "data.lance").write_bytes(b"lance-data")

        mgr = BackupManager(_make_config(), lance_base_uri=tmp_path / "data", blob_store=blob_store)

        with patch("lancedb.connect") as mock_connect:
            mock_ds = MagicMock()
            mock_ds.count_rows.return_value = 50
            mock_conn = MagicMock()
            mock_conn.open_table.return_value = mock_ds
            mock_connect.return_value = mock_conn

            info = mgr.create_backup(dataset_names=["my_dataset"], backup_id="test-001")

        assert info.backup_id == "test-001"
        assert info.datasets == ("my_dataset",)
        assert info.status == "complete"

    def test_create_backup_with_blob_prefixes(self, tmp_path) -> None:
        blob_store = _make_blob_store()
        blob_store.upload.return_value = MagicMock(etag="e", size_bytes=0)
        blob_store.list_blobs.return_value = MagicMock(keys=(), count=0, truncated=False)
        blob_store.download.return_value = b"blob-data"

        mgr = BackupManager(_make_config(), lance_base_uri=tmp_path / "data", blob_store=blob_store)

        info = mgr.create_backup(blob_prefixes=["media/images/"], backup_id="test-002")

        assert info.backup_id == "test-002"
        assert info.blob_prefixes == ("media/images/",)

    def test_create_backup_empty(self, tmp_path) -> None:
        blob_store = _make_blob_store()
        blob_store.upload.return_value = MagicMock(etag="e", size_bytes=0)
        blob_store.list_blobs.return_value = MagicMock(keys=(), count=0, truncated=False)

        mgr = BackupManager(_make_config(), lance_base_uri=tmp_path / "data", blob_store=blob_store)

        info = mgr.create_backup(backup_id="test-003")

        assert info.datasets == ()
        assert info.blob_prefixes == ()
        assert info.status == "complete"

    def test_create_backup_dataset_not_found(self, tmp_path) -> None:
        blob_store = _make_blob_store()
        blob_store.upload.return_value = MagicMock(etag="e", size_bytes=0)
        blob_store.list_blobs.return_value = MagicMock(keys=(), count=0, truncated=False)

        mgr = BackupManager(_make_config(), lance_base_uri=tmp_path / "data", blob_store=blob_store)

        info = mgr.create_backup(dataset_names=["nonexistent"], backup_id="test-004")

        # Partial — dataset missing but manifest still created
        assert info.status == "partial"
        assert info.datasets == ()

    def test_create_backup_storage_error_sets_partial(self, tmp_path) -> None:
        """StorageError during dataset backup sets partial, doesn't re-raise."""
        blob_store = _make_blob_store()

        # First call (dataset file upload) fails; subsequent calls succeed
        blob_store.upload.side_effect = [
            StorageError(
                error_code=ErrorCode.BLOB_UPLOAD_FAILED,
                message="upload failed",
            ),
            MagicMock(etag="e", size_bytes=0),  # manifest upload
        ]
        blob_store.list_blobs.return_value = MagicMock(keys=(), count=0, truncated=False)

        ds_path = tmp_path / "data" / "my_ds"
        ds_path.mkdir(parents=True)
        (ds_path / "data.lance").write_bytes(b"data")

        mgr = BackupManager(_make_config(), lance_base_uri=tmp_path / "data", blob_store=blob_store)

        info = mgr.create_backup(dataset_names=["my_ds"], backup_id="test-partial-storage")

        assert info.status == "partial"


# ---------------------------------------------------------------------------
# BackupManager.restore_backup
# ---------------------------------------------------------------------------


class TestRestoreBackup:
    def test_restore_backup(self, tmp_path) -> None:
        manifest = _make_manifest(
            backup_id="restore-001",
            datasets=[{"name": "my_ds", "rows": 50}],
        )
        manifest_json = manifest.to_json()

        blob_store = _make_blob_store()
        blob_store.download.return_value = manifest_json.encode("utf-8")
        blob_store.list_blobs.return_value = MagicMock(
            keys=("backups/restore-001/datasets/my_ds/data.lance",),
            count=1,
            truncated=False,
        )

        mgr = BackupManager(_make_config(), lance_base_uri=tmp_path / "data", blob_store=blob_store)

        info = mgr.restore_backup("restore-001")

        assert info.backup_id == "restore-001"
        assert info.status == "restored"

    def test_restore_backup_not_found(self, tmp_path) -> None:
        blob_store = _make_blob_store()

        blob_store.download.side_effect = StorageError(
            error_code=ErrorCode.BLOB_NOT_FOUND,
            message="not found",
        )

        mgr = BackupManager(_make_config(), lance_base_uri=tmp_path / "data", blob_store=blob_store)

        with pytest.raises(StorageError) as exc_info:
            mgr.restore_backup("nonexistent")
        assert exc_info.value.error_code == ErrorCode.BLOB_NOT_FOUND

    def test_restore_backup_to_existing_without_overwrite(self, tmp_path) -> None:
        """Restoring to existing dataset without overwrite=True raises StorageError."""
        manifest = _make_manifest(
            backup_id="restore-002",
            datasets=[{"name": "existing_ds", "rows": 10}],
        )
        manifest_json = manifest.to_json()

        blob_store = _make_blob_store()
        blob_store.download.return_value = manifest_json.encode("utf-8")
        blob_store.list_blobs.return_value = MagicMock(
            keys=("backups/restore-002/datasets/existing_ds/data.lance",),
            count=1,
            truncated=False,
        )

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "existing_ds").mkdir()

        mgr = BackupManager(_make_config(), lance_base_uri=data_dir, blob_store=blob_store)

        with pytest.raises(StorageError) as exc_info:
            mgr.restore_backup("restore-002")
        assert exc_info.value.error_code == ErrorCode.STORAGE_WRITE_FAILED


# ---------------------------------------------------------------------------
# BackupManager.list_backups / get_backup_info / delete_backup
# ---------------------------------------------------------------------------


class TestListBackups:
    def test_list_backups_empty(self, tmp_path) -> None:
        blob_store = _make_blob_store()
        blob_store.list_blobs.return_value = MagicMock(keys=(), count=0, truncated=False, next_token=None)

        mgr = BackupManager(_make_config(), lance_base_uri=tmp_path / "data", blob_store=blob_store)

        result = mgr.list_backups()
        assert result == []

    def test_list_backups(self, tmp_path) -> None:
        blob_store = _make_blob_store()
        blob_store.list_blobs.return_value = MagicMock(
            keys=(
                "backups/20260101T000000Zabc/manifest.json",
                "backups/20260102T000000Zdef/manifest.json",
            ),
            count=2,
            truncated=False,
            next_token=None,
        )
        manifest = _make_manifest()
        blob_store.download.return_value = manifest.to_json().encode("utf-8")

        mgr = BackupManager(_make_config(), lance_base_uri=tmp_path / "data", blob_store=blob_store)

        result = mgr.list_backups()
        assert len(result) == 2

    def test_get_backup_info(self, tmp_path) -> None:
        manifest = _make_manifest()
        blob_store = _make_blob_store()
        blob_store.download.return_value = manifest.to_json().encode("utf-8")

        mgr = BackupManager(_make_config(), lance_base_uri=tmp_path / "data", blob_store=blob_store)

        info = mgr.get_backup_info("20260101T000000Zdeadbeef")
        assert info.backup_id == "20260101T000000Zdeadbeef"
        assert info.datasets == ("test_ds",)

    def test_delete_backup(self, tmp_path) -> None:
        blob_store = _make_blob_store()
        blob_store.delete_prefix.return_value = 5

        mgr = BackupManager(_make_config(), lance_base_uri=tmp_path / "data", blob_store=blob_store)

        mgr.delete_backup("20260101T000000Zabc")

        blob_store.delete_prefix.assert_called_once_with("backups/20260101T000000Zabc/")


# ---------------------------------------------------------------------------
# Backup ID generation
# ---------------------------------------------------------------------------


class TestBackupIdGeneration:
    def test_generate_backup_id_format(self) -> None:
        bid = BackupManager._generate_backup_id()
        # Format: YYYYMMDDTHHMMSS + z + 8 hex chars = 24 chars total
        assert len(bid) == 24
        assert "T" in bid
        assert "z" in bid  # separator before random suffix

    def test_generate_unique_ids(self) -> None:
        ids = {BackupManager._generate_backup_id() for _ in range(100)}
        assert len(ids) == 100  # All unique

    def test_backup_id_includes_random_suffix(self) -> None:
        id1 = BackupManager._generate_backup_id()
        id2 = BackupManager._generate_backup_id()
        # Timestamps might be same second, but random suffix differs
        assert id1 != id2
