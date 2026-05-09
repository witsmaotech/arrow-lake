"""Tests for backup path traversal prevention (Round 4 — C3 fix)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from arrow_lake.ops.backup import BackupManager


def _make_manager(tmp_path: Path) -> BackupManager:
    from arrow_lake.config._enums import StorageBackend
    from arrow_lake.config.storage import StorageConfig

    config = MagicMock(spec=StorageConfig)
    config.s3_endpoint = None
    config.s3_bucket = "test-bucket"
    config.backend = StorageBackend.LOCAL
    return BackupManager(
        storage_config=config,
        lance_base_uri=tmp_path,
        blob_store=MagicMock(),
    )


class TestBackupPathValidation:
    """Verify path traversal prevention in backup operations."""

    def test_rejects_dot_dot_in_dataset_name(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(ValueError, match="path traversal"):
            mgr._backup_lance_dataset("../escape", "backup/")

    def test_rejects_slash_in_dataset_name(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(ValueError, match="path traversal"):
            mgr._backup_lance_dataset("sub/dir", "backup/")

    def test_rejects_backslash_in_dataset_name(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(ValueError, match="path traversal"):
            mgr._backup_lance_dataset("sub\\dir", "backup/")

    def test_rejects_resolve_escape(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(ValueError, match="path traversal"):
            mgr._backup_lance_dataset("../../../etc", "backup/")

    def test_rejects_nonexistent_dataset(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(FileNotFoundError, match="Dataset not found"):
            mgr._backup_lance_dataset("nonexistent", "backup/")

    def test_resolve_escape_beyond_base(self, tmp_path):
        mgr = _make_manager(tmp_path / "data")
        with pytest.raises(ValueError, match="path traversal"):
            mgr._backup_lance_dataset("../../escape", "backup/")
