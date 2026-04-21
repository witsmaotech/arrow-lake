"""Integration tests for BackupManager against MinIO.

Requires MinIO running at localhost:9000 (default docker-compose setup).
Tests are skipped if MinIO is unreachable.
"""

from __future__ import annotations

import os

import pytest
from arrow_lake.config import StorageBackend, StorageConfig
from arrow_lake.ops.backup import BackupManager
from arrow_lake.storage.blob_store import BlobStoreManager


def _minio_available() -> bool:
    import boto3
    from botocore.exceptions import ClientError

    try:
        client = boto3.client(
            "s3",
            endpoint_url="http://localhost:9000",
            aws_access_key_id="minioadmin",
            aws_secret_access_key="minioadmin",
            region_name="us-east-1",
        )
        client.head_bucket(Bucket="arrow-lake")
        return True
    except (ClientError, Exception):
        return False


def _make_config() -> StorageConfig:
    return StorageConfig(
        backend=StorageBackend.MINIO,
        s3_endpoint=os.environ.get("S3_ENDPOINT", "http://localhost:9000"),
        s3_access_key=os.environ.get("S3_ACCESS_KEY", "minioadmin"),
        s3_secret_key=os.environ.get("S3_SECRET_KEY", "minioadmin"),
        s3_bucket=os.environ.get("S3_BUCKET", "arrow-lake"),
        s3_region="us-east-1",
    )


@pytest.fixture(scope="module")
def backup_data_dir(tmp_path_factory):
    """Provide a temporary directory for Lance datasets."""
    return tmp_path_factory.mktemp("lance_data")


@pytest.fixture(scope="module")
def backup_mgr(backup_data_dir) -> BackupManager:
    if not _minio_available():
        pytest.skip("MinIO not available at localhost:9000")

    config = _make_config()
    blob_store = BlobStoreManager(config)

    # Clean up any previous test backups
    blob_store.delete_prefix("backups/")

    return BackupManager(config, lance_base_uri=backup_data_dir, blob_store=blob_store)


@pytest.fixture(autouse=True)
def _cleanup(backup_mgr: BackupManager) -> None:
    yield
    import contextlib

    with contextlib.suppress(Exception):
        backup_mgr.blob_store.delete_prefix("backups/test-int-")


# ---------------------------------------------------------------------------
# Backup / Restore round-trip
# ---------------------------------------------------------------------------


class TestBackupRestoreRoundTrip:
    def test_backup_and_list(self, backup_mgr: BackupManager, backup_data_dir) -> None:
        ds_path = backup_data_dir / "test_dataset"
        ds_path.mkdir(parents=True)
        (ds_path / "data.lance").write_bytes(b"fake-lance-data")

        # Create backup
        info = backup_mgr.create_backup(
            dataset_names=["test_dataset"],
            backup_id="test-int-001",
        )

        assert info.backup_id == "test-int-001"
        assert info.datasets == ("test_dataset",)
        assert info.status == "complete"

        # List backups
        backups = backup_mgr.list_backups()
        assert len(backups) >= 1
        assert any(b.backup_id == "test-int-001" for b in backups)

    def test_backup_info(self, backup_mgr: BackupManager, backup_data_dir) -> None:
        ds_path = backup_data_dir / "info_ds"
        ds_path.mkdir(parents=True)
        (ds_path / "data.lance").write_bytes(b"data")

        backup_mgr.create_backup(
            dataset_names=["info_ds"],
            backup_id="test-int-002",
        )

        info = backup_mgr.get_backup_info("test-int-002")
        assert info.backup_id == "test-int-002"
        assert "info_ds" in info.datasets

    def test_delete_backup(self, backup_mgr: BackupManager, backup_data_dir) -> None:
        ds_path = backup_data_dir / "del_ds"
        ds_path.mkdir(parents=True)
        (ds_path / "data.lance").write_bytes(b"data")

        backup_mgr.create_backup(
            dataset_names=["del_ds"],
            backup_id="test-int-003",
        )

        # Verify it exists
        assert backup_mgr.get_backup_info("test-int-003").backup_id == "test-int-003"

        # Delete it
        backup_mgr.delete_backup("test-int-003")

        # Verify it's gone
        from arrow_lake.exceptions import ErrorCode, StorageError

        with pytest.raises(StorageError) as exc_info:
            backup_mgr.get_backup_info("test-int-003")
        assert exc_info.value.error_code == ErrorCode.BLOB_NOT_FOUND

    def test_backup_blob_prefix(self, backup_mgr: BackupManager) -> None:
        # Upload some test blobs
        backup_mgr.blob_store.upload("test-int-blobs/a.txt", b"file-a")
        backup_mgr.blob_store.upload("test-int-blobs/b.txt", b"file-b")

        info = backup_mgr.create_backup(
            blob_prefixes=["test-int-blobs/"],
            backup_id="test-int-004",
        )

        assert info.blob_prefixes == ("test-int-blobs/",)

        # Verify blob data was backed up
        backed_up = backup_mgr.blob_store.list_blobs(
            "backups/test-int-004/blobs/test-int-blobs/"
        )
        assert backed_up.count >= 2
