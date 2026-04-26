"""Tests for backup/restore facade on Lake admin mixin."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from arrow_lake._lake_admin import _LakeAdminMixin
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.ops.backup import BackupInfo


def _make_lake():
    config = ArrowLakeConfig()
    obj = _LakeAdminMixin()
    obj._config = config
    obj._base_uri = "./data"
    obj._components = {}
    obj._get_storage = MagicMock()
    obj._get_component = MagicMock()
    return obj


@pytest.fixture
def lake():
    return _make_lake()


class TestBackupCreate:
    def test_delegates_to_manager(self, lake):
        mock_mgr = MagicMock()
        mock_info = BackupInfo(
            backup_id="b1",
            created_at="2026-01-01",
            datasets=("ds1",),
            blob_prefixes=(),
            total_size_bytes=100,
            status="complete",
        )
        mock_mgr.create_backup.return_value = mock_info

        with patch("arrow_lake.ops.backup.BackupManager", return_value=mock_mgr):
            result = lake.backup_create(["ds1"])
            assert result.backup_id == "b1"
            mock_mgr.create_backup.assert_called_once()

    def test_initializes_blob_store(self, lake):
        with patch("arrow_lake.ops.backup.BackupManager"):
            lake.backup_create()
            lake._get_component.assert_called()


class TestBackupRestore:
    def test_delegates_to_manager(self, lake):
        mock_mgr = MagicMock()
        mock_info = BackupInfo(
            backup_id="b1",
            created_at="2026-01-01",
            datasets=("ds1",),
            blob_prefixes=(),
            total_size_bytes=100,
            status="complete",
        )
        mock_mgr.restore_backup.return_value = mock_info

        with patch("arrow_lake.ops.backup.BackupManager", return_value=mock_mgr):
            result = lake.backup_restore("b1")
            assert result.backup_id == "b1"


class TestBackupList:
    def test_delegates_to_manager(self, lake):
        mock_mgr = MagicMock()
        mock_mgr.list_backups.return_value = []

        with patch("arrow_lake.ops.backup.BackupManager", return_value=mock_mgr):
            result = lake.backup_list()
            assert result == []


class TestBackupDelete:
    def test_delegates_to_manager(self, lake):
        mock_mgr = MagicMock()

        with patch("arrow_lake.ops.backup.BackupManager", return_value=mock_mgr):
            lake.backup_delete("b1")
            mock_mgr.delete_backup.assert_called_once_with("b1")
