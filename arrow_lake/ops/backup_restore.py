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

from arrow_lake.exceptions import StorageError, ErrorCode

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

    def __init__(self, blob_store: Any, lance_base_uri: str) -> None:
        self._blob_store = blob_store
        self._lance_base_uri = lance_base_uri

    def restore_lance_dataset(
        self,
        dataset_name: str,
        manifest: Any,
        *,
        overwrite: bool = False,
    ) -> None:
        """Restore a Lance dataset from backup.

        Uses atomic temp-dir + rename to avoid TOCTOU race conditions.
        """
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
