"""Coverage for BackupRestorer — restore lance datasets and blob prefixes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.config import StorageBackend
from arrow_lake.ops.backup_restore import BackupRestorer


@dataclass(frozen=True)
class _FakeListResult:
    keys: list[str]
    next_token: str | None = None
    truncated: bool = False
    count: int = 0


@dataclass
class _FakeManifest:
    backup_id: str = "test-backup"
    datasets: list = None
    blob_prefixes: list = None

    def __post_init__(self):
        if self.datasets is None:
            self.datasets = [{"name": "ds1", "rows": 10, "file_hashes": {}}]


def _make_blob_store() -> MagicMock:
    bs = MagicMock()
    bs.list_blobs.return_value = _FakeListResult(keys=[])
    bs.download.return_value = b"test data"
    bs.upload.return_value = MagicMock(key="key")
    bs.copy.return_value = None
    return bs


@pytest.fixture
def blob_store() -> MagicMock:
    return _make_blob_store()


@pytest.fixture
def restorer(blob_store: MagicMock) -> BackupRestorer:
    cfg = MagicMock()
    cfg.backend = StorageBackend.LOCAL
    return BackupRestorer(blob_store=blob_store, lance_base_uri="/tmp/test-lake", storage_config=cfg)


# ── restore_blob_prefix ──


class TestRestoreBlobPrefix:
    def test_empty_prefix(self, restorer: BackupRestorer, blob_store: MagicMock) -> None:
        blob_store.list_blobs.return_value = _FakeListResult(keys=[])
        manifest = _FakeManifest()
        restorer.restore_blob_prefix("uploads/ds/", manifest)
        blob_store.upload.assert_not_called()

    def test_restores_blobs(self, restorer: BackupRestorer, blob_store: MagicMock) -> None:
        blob_store.list_blobs.return_value = _FakeListResult(
            keys=["backups/test-backup/blobs/uploads/ds/file1.csv"],
        )
        manifest = _FakeManifest()
        restorer.restore_blob_prefix("uploads/ds/", manifest)
        blob_store.upload.assert_called_once()

    def test_pagination(self, restorer: BackupRestorer, blob_store: MagicMock) -> None:
        blob_store.list_blobs.side_effect = [
            _FakeListResult(keys=["key1"], truncated=True, next_token="tok1"),
            _FakeListResult(keys=["key2"], truncated=False),
        ]
        manifest = _FakeManifest()
        restorer.restore_blob_prefix("up/", manifest)
        assert blob_store.upload.call_count == 2


# ── restore_lance_dataset (remote) ──


class TestRestoreLanceDatasetRemote:
    def test_remote_restore(self, blob_store: MagicMock) -> None:
        cfg = MagicMock()
        cfg.backend = StorageBackend.S3
        cfg.base_uri = "s3://bucket"
        r = BackupRestorer(blob_store=blob_store, lance_base_uri="s3://bucket", storage_config=cfg)
        blob_store.list_blobs.return_value = _FakeListResult(keys=[], count=0)
        manifest = _FakeManifest()
        r.restore_lance_dataset("ds1", manifest)  # Should not raise


# ── restore_lance_dataset (local) ──


class TestRestoreLanceDatasetLocal:
    def test_local_already_exists_no_overwrite(self, restorer: BackupRestorer, blob_store: MagicMock, tmp_path) -> None:
        from arrow_lake.exceptions import StorageError

        # Create a dataset dir that already exists
        ds_path = tmp_path / "ds1"
        ds_path.mkdir()

        r = BackupRestorer(blob_store=blob_store, lance_base_uri=str(tmp_path), storage_config=restorer._storage_config)
        manifest = _FakeManifest()

        with pytest.raises(StorageError, match="already exists"):
            r.restore_lance_dataset("ds1", manifest, overwrite=False)

    def test_local_restore_with_overwrite(self, restorer: BackupRestorer, blob_store: MagicMock, tmp_path) -> None:
        blob_store.list_blobs.return_value = _FakeListResult(keys=["backups/test-backup/datasets/new_ds/data.lance"])
        blob_store.download.return_value = b"fake lance data"
        r = BackupRestorer(blob_store=blob_store, lance_base_uri=str(tmp_path), storage_config=restorer._storage_config)
        manifest = _FakeManifest()
        r.restore_lance_dataset("new_ds", manifest, overwrite=True)

    def test_local_checksum_mismatch(self, tmp_path: Any, blob_store: MagicMock) -> None:
        """Covers lines 95-105: checksum mismatch raises StorageError."""
        import hashlib

        from arrow_lake.exceptions import StorageError

        correct_hash = hashlib.sha256(b"original data").hexdigest()
        manifest = _FakeManifest()
        manifest.datasets = [{"name": "myds", "file_hashes": {"data.lance": correct_hash}}]

        blob_store.list_blobs.return_value = _FakeListResult(
            keys=["backups/test-backup/datasets/myds/data.lance"],
        )
        # download returns different data so hash won't match
        blob_store.download.return_value = b"corrupted data"

        cfg = MagicMock()
        cfg.backend = StorageBackend.LOCAL
        r = BackupRestorer(blob_store=blob_store, lance_base_uri=str(tmp_path), storage_config=cfg)

        with pytest.raises(StorageError, match="Checksum mismatch"):
            r.restore_lance_dataset("myds", manifest, overwrite=True)

    def test_local_temp_cleanup_on_storage_error(self, tmp_path: Any, blob_store: MagicMock) -> None:
        """Covers lines 120-123: tmp_path cleaned up when StorageError propagates."""
        from arrow_lake.exceptions import StorageError

        manifest = _FakeManifest()
        manifest.datasets = [{"name": "myds", "file_hashes": {"f.txt": "badhash"}}]

        blob_store.list_blobs.return_value = _FakeListResult(
            keys=["backups/test-backup/datasets/myds/f.txt"],
        )
        blob_store.download.return_value = b"some data"

        cfg = MagicMock()
        cfg.backend = StorageBackend.LOCAL
        r = BackupRestorer(blob_store=blob_store, lance_base_uri=str(tmp_path), storage_config=cfg)

        with pytest.raises(StorageError, match="Checksum mismatch"):
            r.restore_lance_dataset("myds", manifest, overwrite=True)

        # Verify temp dir was cleaned up
        tmp_dir = tmp_path / ".myds.restore-"
        # os.getpid() varies, just check no leftover temp dirs
        tmp_dirs = [p for p in tmp_path.iterdir() if p.name.startswith(".myds.restore-")]
        assert len(tmp_dirs) == 0

    def test_local_temp_cleanup_on_oserror(self, tmp_path: Any, blob_store: MagicMock) -> None:
        """Covers lines 124-130: OSError during restore cleans up temp and re-raises."""
        from arrow_lake.exceptions import StorageError

        blob_store.list_blobs.return_value = _FakeListResult(
            keys=["backups/test-backup/datasets/myds/data.lance"],
        )
        blob_store.download.return_value = b"data"

        # First call to mkdir succeeds; make write_bytes raise OSError
        blob_store.download.side_effect = OSError("disk full")

        cfg = MagicMock()
        cfg.backend = StorageBackend.LOCAL
        r = BackupRestorer(blob_store=blob_store, lance_base_uri=str(tmp_path), storage_config=cfg)
        manifest = _FakeManifest()

        with pytest.raises(StorageError, match="Failed to restore dataset"):
            r.restore_lance_dataset("myds", manifest, overwrite=True)

        tmp_dirs = [p for p in tmp_path.iterdir() if p.name.startswith(".myds.restore-")]
        assert len(tmp_dirs) == 0

    def test_local_pagination_with_continuation_token(self, tmp_path: Any, blob_store: MagicMock) -> None:
        """Covers lines 83-115: local restore handles paginated list_blobs."""
        blob_store.list_blobs.side_effect = [
            _FakeListResult(
                keys=["backups/test-backup/datasets/pds/part1.lance"],
                truncated=True,
                next_token="token-1",
            ),
            _FakeListResult(
                keys=["backups/test-backup/datasets/pds/part2.lance"],
                truncated=True,
                next_token="token-2",
            ),
            _FakeListResult(
                keys=["backups/test-backup/datasets/pds/part3.lance"],
                truncated=False,
            ),
        ]
        blob_store.download.return_value = b"page-data"

        cfg = MagicMock()
        cfg.backend = StorageBackend.LOCAL
        r = BackupRestorer(blob_store=blob_store, lance_base_uri=str(tmp_path), storage_config=cfg)
        manifest = _FakeManifest()

        r.restore_lance_dataset("pds", manifest, overwrite=True)

        # Verify 3 pages were fetched and 3 files written
        assert blob_store.list_blobs.call_count == 3
        assert blob_store.download.call_count == 3
        dest = tmp_path / "pds"
        assert dest.exists()
        assert (dest / "part1.lance").exists()
        assert (dest / "part2.lance").exists()
        assert (dest / "part3.lance").exists()

    def test_local_pagination_no_keys_breaks(self, tmp_path: Any, blob_store: MagicMock) -> None:
        """Covers line 114-115: if truncated but keys empty, break the loop."""
        blob_store.list_blobs.side_effect = [
            _FakeListResult(
                keys=["backups/test-backup/datasets/pds/file1"],
                truncated=True,
                next_token="t1",
            ),
            _FakeListResult(keys=[], truncated=True, next_token="t2"),
        ]
        blob_store.download.return_value = b"data"

        cfg = MagicMock()
        cfg.backend = StorageBackend.LOCAL
        r = BackupRestorer(blob_store=blob_store, lance_base_uri=str(tmp_path), storage_config=cfg)
        manifest = _FakeManifest()

        r.restore_lance_dataset("pds", manifest, overwrite=True)

        assert blob_store.list_blobs.call_count == 2
        assert blob_store.download.call_count == 1


# ── restore_lance_dataset (remote) — additional coverage ──


class TestRestoreLanceDatasetRemoteExtended:
    """Extended coverage for remote restore paths."""

    def _make_remote_restorer(self, blob_store: MagicMock, base_uri: str = "s3://bucket") -> BackupRestorer:
        cfg = MagicMock()
        cfg.backend = StorageBackend.S3
        cfg.base_uri = base_uri
        return BackupRestorer(blob_store=blob_store, lance_base_uri=base_uri, storage_config=cfg)

    def test_remote_overwrite_deletes_prefix(self, blob_store: MagicMock) -> None:
        """Covers line 149: overwrite=True calls delete_prefix before restore."""
        blob_store.list_blobs.return_value = _FakeListResult(keys=[], count=0)
        manifest = _FakeManifest()

        r = self._make_remote_restorer(blob_store)
        r.restore_lance_dataset("ds1", manifest, overwrite=True)

        blob_store.delete_prefix.assert_called_once_with("s3://bucket/ds1.lance/")

    def test_remote_already_exists_no_overwrite(self, blob_store: MagicMock) -> None:
        """Covers lines 141-147: remote restore raises when dataset exists and overwrite=False."""
        from arrow_lake.exceptions import StorageError

        blob_store.list_blobs.return_value = _FakeListResult(keys=["s3://bucket/ds1.lance/data"], count=1)
        manifest = _FakeManifest()

        r = self._make_remote_restorer(blob_store)
        with pytest.raises(StorageError, match="already exists"):
            r.restore_lance_dataset("ds1", manifest, overwrite=False)

    def test_remote_copy_path_no_hashes(self, blob_store: MagicMock) -> None:
        """Covers line 176: when no hashes, uses copy instead of download+upload."""
        manifest = _FakeManifest()
        manifest.datasets = [{"name": "cds", "file_hashes": {}}]

        blob_store.list_blobs.return_value = _FakeListResult(
            keys=["backups/test-backup/datasets/cds/data.lance"],
        )
        blob_store.copy.return_value = None

        r = self._make_remote_restorer(blob_store)
        r.restore_lance_dataset("cds", manifest, overwrite=True)

        blob_store.copy.assert_called_once_with(
            "backups/test-backup/datasets/cds/data.lance",
            "s3://bucket/cds.lance/data.lance",
        )
        blob_store.download.assert_not_called()
        blob_store.upload.assert_not_called()

    def test_remote_checksum_mismatch(self, blob_store: MagicMock) -> None:
        """Covers lines 166-173: remote checksum mismatch raises StorageError."""
        import hashlib

        from arrow_lake.exceptions import StorageError

        correct_hash = hashlib.sha256(b"good data").hexdigest()
        manifest = _FakeManifest()
        manifest.datasets = [{"name": "hds", "file_hashes": {"data.lance": correct_hash}}]

        blob_store.list_blobs.return_value = _FakeListResult(
            keys=["backups/test-backup/datasets/hds/data.lance"],
        )
        blob_store.download.return_value = b"bad data"

        r = self._make_remote_restorer(blob_store)
        with pytest.raises(StorageError, match="Checksum mismatch"):
            r.restore_lance_dataset("hds", manifest, overwrite=True)

    def test_remote_checksum_pass(self, blob_store: MagicMock) -> None:
        """Covers lines 166-174: remote checksum matches, uses download+upload."""
        import hashlib

        correct_hash = hashlib.sha256(b"good data").hexdigest()
        manifest = _FakeManifest()
        manifest.datasets = [{"name": "gds", "file_hashes": {"data.lance": correct_hash}}]

        blob_store.list_blobs.return_value = _FakeListResult(
            keys=["backups/test-backup/datasets/gds/data.lance"],
        )
        blob_store.download.return_value = b"good data"
        blob_store.upload.return_value = None

        r = self._make_remote_restorer(blob_store)
        r.restore_lance_dataset("gds", manifest, overwrite=True)

        blob_store.download.assert_called_once()
        blob_store.upload.assert_called_once()
        blob_store.copy.assert_not_called()

    def test_remote_runtime_error_wrapped(self, blob_store: MagicMock) -> None:
        """Covers lines 185-189: RuntimeError during remote restore wrapped as StorageError."""
        from arrow_lake.exceptions import StorageError

        blob_store.list_blobs.side_effect = RuntimeError("connection refused")
        manifest = _FakeManifest()

        r = self._make_remote_restorer(blob_store)
        with pytest.raises(StorageError, match="Failed to restore dataset.*S3"):
            r.restore_lance_dataset("eds", manifest, overwrite=True)

    def test_remote_oserror_wrapped(self, blob_store: MagicMock) -> None:
        """Covers lines 185-189: OSError during remote restore wrapped as StorageError."""
        from arrow_lake.exceptions import StorageError

        blob_store.list_blobs.side_effect = OSError("network")
        manifest = _FakeManifest()

        r = self._make_remote_restorer(blob_store)
        with pytest.raises(StorageError, match="Failed to restore dataset.*S3"):
            r.restore_lance_dataset("ods", manifest, overwrite=True)

    def test_remote_storage_error_propagated(self, blob_store: MagicMock) -> None:
        """Covers lines 183-184: StorageError during remote restore is re-raised as-is."""
        from arrow_lake.exceptions import ErrorCode, StorageError

        blob_store.list_blobs.side_effect = StorageError(
            error_code=ErrorCode.STORAGE_READ_FAILED,
            message="original error",
        )
        manifest = _FakeManifest()

        r = self._make_remote_restorer(blob_store)
        with pytest.raises(StorageError, match="original error"):
            r.restore_lance_dataset("sds", manifest, overwrite=True)

    def test_remote_base_uri_strip_dot_slash(self, blob_store: MagicMock) -> None:
        """Covers lines 137-139: base_uri starting with './' gets stripped."""
        blob_store.list_blobs.return_value = _FakeListResult(keys=[], count=0)
        manifest = _FakeManifest()

        r = self._make_remote_restorer(blob_store, base_uri="./bucket/data")
        r.restore_lance_dataset("ds1", manifest, overwrite=True)

        blob_store.delete_prefix.assert_called_once_with("bucket/data/ds1.lance/")

    def test_remote_pagination_no_keys_breaks(self, blob_store: MagicMock) -> None:
        """Covers lines 180-182: remote pagination breaks when truncated but keys empty."""
        manifest = _FakeManifest()
        manifest.datasets = [{"name": "pds", "file_hashes": {}}]

        blob_store.list_blobs.side_effect = [
            _FakeListResult(
                keys=["backups/test-backup/datasets/pds/file1"],
                truncated=True,
                next_token="t1",
            ),
            _FakeListResult(keys=[], truncated=True, next_token="t2"),
        ]

        r = self._make_remote_restorer(blob_store)
        r.restore_lance_dataset("pds", manifest, overwrite=True)

        assert blob_store.list_blobs.call_count == 2
        blob_store.copy.assert_called_once()


# ── restore_blob_prefix — additional coverage ──


class TestRestoreBlobPrefixExtended:
    def test_pagination_no_keys_breaks(self, restorer: BackupRestorer, blob_store: MagicMock) -> None:
        """Covers line 213: pagination breaks when truncated but keys is empty."""
        blob_store.list_blobs.side_effect = [
            _FakeListResult(
                keys=["backups/test-backup/blobs/up/file1"],
                truncated=True,
                next_token="t1",
            ),
            _FakeListResult(keys=[], truncated=True, next_token="t2"),
        ]
        manifest = _FakeManifest()
        restorer.restore_blob_prefix("up/", manifest)

        assert blob_store.list_blobs.call_count == 2
        assert blob_store.upload.call_count == 1
