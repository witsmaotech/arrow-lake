"""Dataset and blob restore operations.

Extracted from BackupManager to keep backup.py under 500 lines.
This module is internal — use BackupManager.restore_backup() instead.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from typing import Any

import structlog

from arrow_lake.config import StorageBackend
from arrow_lake.exceptions import ErrorCode, StorageError

_log = structlog.get_logger(__name__)

_BACKUP_PREFIX = "backups/"
_MANIFEST_FILENAME = "manifest.json"


class BackupRestorer:
    """Handles restore operations for Arrow Lake backups.

    Separated from BackupManager to keep concerns focused and
    the backup module under 500 lines.

    Args:
        blob_store: BlobStoreManager instance.
        lance_base_uri: Base URI where Lance datasets live.
    """

    def __init__(self, blob_store: Any, lance_base_uri: str, storage_config: Any = None) -> None:
        self._blob_store = blob_store
        self._lance_base_uri = lance_base_uri
        self._storage_config = storage_config

    def restore_lance_dataset(
        self,
        dataset_name: str,
        manifest: Any,
        *,
        overwrite: bool = False,
    ) -> None:
        """Restore a Lance dataset from backup.

        Dispatches to local or remote (S3) restore based on storage config.
        """
        is_remote = (
            self._storage_config is not None
            and self._storage_config.backend != StorageBackend.LOCAL
        )
        if is_remote:
            self._restore_lance_dataset_remote(dataset_name, manifest, overwrite=overwrite)
            return

        # --- Local filesystem restore ---
        from pathlib import Path

        dest_path = Path(self._lance_base_uri) / dataset_name

        if dest_path.exists() and not overwrite:
            raise StorageError(
                error_code=ErrorCode.STORAGE_WRITE_FAILED,
                message=f"Dataset '{dataset_name}' already exists. Use overwrite=True.",
            )

        backup_prefix = f"{_BACKUP_PREFIX}{manifest.backup_id}/datasets/{dataset_name}/"

        ds_entry = next((d for d in manifest.datasets if d["name"] == dataset_name), None)
        expected_hashes: dict[str, str] = {}
        if ds_entry and "file_hashes" in ds_entry:
            expected_hashes = ds_entry["file_hashes"]

        tmp_path = dest_path.parent / f".{dataset_name}.restore-{os.getpid()}"
        try:
            if tmp_path.exists():
                shutil.rmtree(tmp_path)

            continuation_token: str | None = None
            while True:
                result = self._blob_store.list_blobs(
                    backup_prefix,
                    max_keys=5000,
                    continuation_token=continuation_token,
                )

                for key in result.keys:
                    data = self._blob_store.download(key)
                    rel_path = key[len(backup_prefix) :]

                    if expected_hashes and rel_path in expected_hashes:
                        actual_hash = hashlib.sha256(data).hexdigest()
                        if actual_hash != expected_hashes[rel_path]:
                            raise StorageError(
                                error_code=ErrorCode.STORAGE_READ_FAILED,
                                message=(
                                    f"Checksum mismatch for '{rel_path}' in dataset "
                                    f"'{dataset_name}': expected {expected_hashes[rel_path]}, "
                                    f"got {actual_hash}"
                                ),
                            )

                    local_path = tmp_path / rel_path
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_bytes(data)

                if not result.truncated:
                    break
                continuation_token = result.next_token
                if not result.keys:
                    break

            if dest_path.exists():
                shutil.rmtree(dest_path)
            tmp_path.rename(dest_path)
        except StorageError:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)
            raise
        except (OSError, shutil.Error) as exc:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)
            raise StorageError(
                error_code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Failed to restore dataset '{dataset_name}': {exc}",
            ) from exc

    def _restore_lance_dataset_remote(
        self, dataset_name: str, manifest: Any, *, overwrite: bool = False,
    ) -> None:
        """Restore a Lance dataset from backup to remote storage (S3/MinIO).

        Safe ordering: download+verify to temp prefix first, then delete
        original and rename temp into place.  If anything fails during
        download/verify the original data is untouched.
        """
        base = self._storage_config.base_uri
        if base.startswith("./"):
            base = base[2:]
        dest_prefix = f"{base}/{dataset_name}.lance/"
        tmp_prefix = f"{base}/.{dataset_name}.lance.restore-{os.getpid()}/"

        if not overwrite:
            probe = self._blob_store.list_blobs(dest_prefix, max_keys=1)
            if probe.count > 0:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=f"Dataset '{dataset_name}' already exists. Use overwrite=True.",
                )

        backup_prefix = f"{_BACKUP_PREFIX}{manifest.backup_id}/datasets/{dataset_name}/"

        ds_entry = next((d for d in manifest.datasets if d["name"] == dataset_name), None)
        expected_hashes: dict[str, str] = ds_entry.get("file_hashes", {}) if ds_entry else {}

        # Phase 1: Download all backup data to a temporary prefix and verify hashes.
        continuation_token: str | None = None
        try:
            # Clean stale temp if it exists
            self._blob_store.delete_prefix(tmp_prefix)

            while True:
                result = self._blob_store.list_blobs(
                    backup_prefix, max_keys=5000, continuation_token=continuation_token,
                )
                for key in result.keys:
                    rel_path = key[len(backup_prefix):]
                    tmp_key = f"{tmp_prefix}{rel_path}"

                    if expected_hashes and rel_path in expected_hashes:
                        data = self._blob_store.download(key)
                        actual_hash = hashlib.sha256(data).hexdigest()
                        if actual_hash != expected_hashes[rel_path]:
                            raise StorageError(
                                error_code=ErrorCode.STORAGE_READ_FAILED,
                                message=f"Checksum mismatch for '{rel_path}'",
                            )
                        self._blob_store.upload(tmp_key, data)
                    else:
                        self._blob_store.copy(key, tmp_key)

                if not result.truncated:
                    break
                continuation_token = result.next_token
                if not result.keys:
                    break

            # Phase 2: Download/verify succeeded — now safe to replace original.
            if overwrite:
                self._blob_store.delete_prefix(dest_prefix)

            # Phase 3: Copy from temp to final destination.
            continuation_token = None
            while True:
                result = self._blob_store.list_blobs(
                    tmp_prefix, max_keys=5000, continuation_token=continuation_token,
                )
                for key in result.keys:
                    rel_path = key[len(tmp_prefix):]
                    dest_key = f"{dest_prefix}{rel_path}"
                    self._blob_store.copy(key, dest_key)

                if not result.truncated:
                    break
                continuation_token = result.next_token
                if not result.keys:
                    break

        except StorageError:
            # Best-effort cleanup of temp prefix on any failure.
            try:
                self._blob_store.delete_prefix(tmp_prefix)
            except Exception:  # noqa: BLE001
                pass
            raise
        except (OSError, RuntimeError) as exc:
            try:
                self._blob_store.delete_prefix(tmp_prefix)
            except Exception:  # noqa: BLE001
                pass
            raise StorageError(
                error_code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Failed to restore dataset '{dataset_name}' to S3: {exc}",
            ) from exc
        else:
            # Cleanup temp prefix after successful restore.
            try:
                self._blob_store.delete_prefix(tmp_prefix)
            except Exception:  # noqa: BLE001
                _log.warning("remote_restore_tmp_cleanup_failed", tmp_prefix=tmp_prefix)

    def restore_blob_prefix(self, src_prefix: str, manifest: Any) -> None:
        """Restore blob data from backup to original prefix (handles pagination)."""
        backup_prefix = f"{_BACKUP_PREFIX}{manifest.backup_id}/blobs/{src_prefix}/"
        continuation_token: str | None = None

        while True:
            result = self._blob_store.list_blobs(
                backup_prefix,
                max_keys=5000,
                continuation_token=continuation_token,
            )

            for key in result.keys:
                data = self._blob_store.download(key)
                rel_path = key[len(backup_prefix) :]
                dest_key = src_prefix + rel_path
                self._blob_store.upload(dest_key, data)

            if not result.truncated:
                break
            continuation_token = result.next_token
            if not result.keys:
                break
