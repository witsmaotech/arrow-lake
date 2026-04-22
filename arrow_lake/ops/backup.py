"""Backup and restore operations — Story M1.

Provides BackupManager for:
- Full dataset backups (Lance data + metadata)
- Blob store backup (MinIO/S3 objects under a prefix)
- Manifest-based restore
- Backup listing, info, and cleanup

Backups are stored as a JSON manifest + data files in a backup
prefix on the configured S3/MinIO bucket.
"""

from __future__ import annotations

import hashlib
import json
import os

_HASH_ALGORITHM = "sha256"
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from arrow_lake.config import StorageConfig
from arrow_lake.exceptions import BackupError, ErrorCode, StorageError
from arrow_lake.storage.blob_store import BlobStoreManager

_log = structlog.get_logger(__name__)

__all__ = ["BackupInfo", "BackupManager", "BackupManifest"]

# Backup prefix in the blob store.
_BACKUP_PREFIX = "backups/"

# Manifest file name stored with each backup.
_MANIFEST_FILENAME = "manifest.json"

# Metadata file stored with each dataset backup.
_METADATA_FILENAME = "_backup_meta.json"


@dataclass(frozen=True)
class BackupInfo:
    """Summary of a backup.

    Attributes:
        backup_id: Unique backup identifier (ISO-8601 timestamp-based).
        created_at: ISO-8601 creation timestamp.
        datasets: Names of datasets included.
        blob_prefixes: S3 prefixes of blob data included.
        total_size_bytes: Approximate total size in bytes.
        status: Backup status ('complete', 'partial', 'failed').
    """

    backup_id: str
    created_at: str
    datasets: tuple[str, ...]
    blob_prefixes: tuple[str, ...]
    total_size_bytes: int
    status: str


@dataclass
class BackupManifest:
    """Internal backup manifest stored alongside backup data.

    Attributes:
        backup_id: Unique backup identifier.
        created_at: ISO-8601 timestamp.
        datasets: List of dataset names and their row counts.
        blob_prefixes: List of S3 prefixes and their object counts.
        lance_base_uri: Base URI where Lance datasets were stored.
        total_size_bytes: Approximate total backup size.
        status: 'complete' or 'partial'.
    """

    backup_id: str
    created_at: str
    datasets: list[dict[str, Any]] = field(default_factory=list)
    blob_prefixes: list[dict[str, Any]] = field(default_factory=list)
    lance_base_uri: str = ""
    total_size_bytes: int = 0
    status: str = "complete"

    def to_json(self) -> str:
        from dataclasses import asdict

        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> BackupManifest:
        data = json.loads(raw)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class BackupManager:
    """Manages backup and restore operations for Arrow Lake.

    Backups consist of:
    1. Lance dataset copies (data files + versions)
    2. Blob store objects (MinIO/S3 prefix snapshots)
    3. A JSON manifest describing backup contents

    Thread safety: safe for concurrent backup creation (each backup
    gets a unique ID). NOT safe for concurrent restore to the same
    destination.

    Args:
        storage_config: Storage configuration.
        lance_base_uri: Base URI where Lance datasets live.
        blob_store: BlobStoreManager instance (None = auto-create).
        backup_bucket: Bucket for storing backups (None = same as data bucket).
    """

    def __init__(
        self,
        storage_config: StorageConfig,
        lance_base_uri: str | Path,
        blob_store: BlobStoreManager | None = None,
        backup_bucket: str | None = None,
    ) -> None:
        self._storage_config = storage_config
        self._lance_base_uri = Path(lance_base_uri)
        self._blob_store = blob_store or BlobStoreManager(storage_config)
        self._backup_bucket = backup_bucket or storage_config.s3_bucket

    @property
    def blob_store(self) -> BlobStoreManager:
        return self._blob_store

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    def create_backup(
        self,
        dataset_names: list[str] | None = None,
        *,
        blob_prefixes: list[str] | None = None,
        backup_id: str | None = None,
    ) -> BackupInfo:
        """Create a backup of Lance datasets and/or blob prefixes.

        Args:
            dataset_names: Datasets to back up (None = all datasets).
            blob_prefixes: S3 prefixes to back up (None = none).
            backup_id: Custom backup ID (None = auto-generate from timestamp).

        Returns:
            BackupInfo with backup metadata.

        Raises:
            StorageError: If backup fails.
        """
        backup_id = backup_id or self._generate_backup_id()
        prefix = f"{_BACKUP_PREFIX}{backup_id}/"
        timestamp = datetime.now(UTC).isoformat()

        manifest = BackupManifest(
            backup_id=backup_id,
            created_at=timestamp,
            lance_base_uri=str(self._lance_base_uri),
        )

        # Backup Lance datasets
        if dataset_names is not None:
            for name in dataset_names:
                try:
                    rows, file_hashes = self._backup_lance_dataset(name, prefix)
                    manifest.datasets.append({
                        "name": name,
                        "rows": rows,
                        "file_hashes": file_hashes,
                    })
                except FileNotFoundError as exc:
                    _log.warning(
                        "backup_dataset_not_found",
                        dataset=name,
                        backup_id=backup_id,
                        error=str(exc),
                    )
                    manifest.status = "partial"
                except StorageError:
                    _log.warning(
                        "backup_dataset_storage_error",
                        dataset=name,
                        backup_id=backup_id,
                        exc_info=True,
                    )
                    manifest.status = "partial"
                except (OSError, RuntimeError) as exc:
                    _log.error(
                        "backup_dataset_unexpected_error",
                        dataset=name,
                        backup_id=backup_id,
                        error=str(exc),
                        exc_info=True,
                    )
                    manifest.status = "partial"

        # Backup blob prefixes
        if blob_prefixes:
            for bp in blob_prefixes:
                try:
                    count = self._backup_blob_prefix(bp, prefix + "blobs/")
                    manifest.blob_prefixes.append({"prefix": bp, "object_count": count})
                except StorageError:
                    _log.warning(
                        "backup_blob_prefix_storage_error",
                        prefix=bp,
                        backup_id=backup_id,
                        exc_info=True,
                    )
                    manifest.status = "partial"
                except StorageError as exc:
                    _log.error(
                        "backup_blob_prefix_unexpected_error",
                        prefix=bp,
                        backup_id=backup_id,
                        error=str(exc),
                        exc_info=True,
                    )
                    manifest.status = "partial"

        # Estimate total size
        total_size = self._estimate_backup_size(prefix)
        manifest.total_size_bytes = total_size

        # Upload manifest (atomic: staging → copy → delete)
        manifest_bytes = manifest.to_json().encode("utf-8")
        staging_key = f"{prefix}{_MANIFEST_FILENAME}.staging"
        final_key = f"{prefix}{_MANIFEST_FILENAME}"
        try:
            self._blob_store.upload(staging_key, manifest_bytes, content_type="application/json")
            self._blob_store.copy(staging_key, final_key)
        finally:
            try:
                self._blob_store.delete(staging_key)
            except (StorageError, OSError):
                pass

        _log.info(
            "backup_created",
            backup_id=backup_id,
            datasets=len(manifest.datasets),
            blob_prefixes=len(manifest.blob_prefixes),
            total_size_bytes=total_size,
            status=manifest.status,
        )

        return BackupInfo(
            backup_id=backup_id,
            created_at=timestamp,
            datasets=tuple(d["name"] for d in manifest.datasets),
            blob_prefixes=tuple(bp["prefix"] for bp in manifest.blob_prefixes),
            total_size_bytes=total_size,
            status=manifest.status,
        )

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore_backup(
        self,
        backup_id: str,
        *,
        dataset_names: list[str] | None = None,
        blob_prefixes: list[str] | None = None,
        overwrite: bool = False,
    ) -> BackupInfo:
        """Restore a backup.

        Args:
            backup_id: Backup identifier to restore.
            dataset_names: Datasets to restore (None = all in backup).
            blob_prefixes: Blob prefixes to restore (None = all in backup).
            overwrite: Whether to overwrite existing datasets.

        Returns:
            BackupInfo of the restored backup.

        Raises:
            BackupError: If backup not found or restore fails.
        """
        manifest = self._load_manifest(backup_id)

        # Restore Lance datasets
        if dataset_names is None:
            dataset_names = [d["name"] for d in manifest.datasets]

        for name in dataset_names:
            try:
                self._restore_lance_dataset(name, manifest, overwrite=overwrite)
            except StorageError:
                raise
            except (OSError, shutil.Error) as exc:
                _log.error(
                    "restore_dataset_failed",
                    dataset=name,
                    backup_id=backup_id,
                    error=str(exc),
                )
                raise BackupError(
                    error_code=ErrorCode.STORAGE_READ_FAILED,
                    message=f"Failed to restore dataset '{name}' from backup '{backup_id}': {exc}",
                ) from exc

        # Restore blob prefixes
        if blob_prefixes is None:
            blob_prefixes = [bp["prefix"] for bp in manifest.blob_prefixes]

        for bp in blob_prefixes:
            try:
                self._restore_blob_prefix(bp, manifest)
            except StorageError:
                raise
            except (OSError, shutil.Error) as exc:
                _log.error(
                    "restore_blob_prefix_failed",
                    prefix=bp,
                    backup_id=backup_id,
                    error=str(exc),
                )
                raise BackupError(
                    error_code=ErrorCode.STORAGE_READ_FAILED,
                    message=f"Failed to restore blob prefix '{bp}' from backup '{backup_id}': {exc}",
                ) from exc

        _log.info(
            "backup_restored",
            backup_id=backup_id,
            datasets=dataset_names,
            blob_prefixes=blob_prefixes,
        )

        return BackupInfo(
            backup_id=manifest.backup_id,
            created_at=manifest.created_at,
            datasets=tuple(dataset_names),
            blob_prefixes=tuple(blob_prefixes),
            total_size_bytes=manifest.total_size_bytes,
            status="restored",
        )

    # ------------------------------------------------------------------
    # List / Info / Delete
    # ------------------------------------------------------------------

    def list_backups(self) -> list[BackupInfo]:
        """List all available backups.

        Paginates through all blobs under the backup prefix.

        Returns:
            List of BackupInfo for each backup found.
        """
        all_keys: list[str] = []
        continuation_token: str | None = None
        while True:
            result = self._blob_store.list_blobs(
                _BACKUP_PREFIX,
                continuation_token=continuation_token,
            )
            all_keys.extend(result.keys)
            continuation_token = result.next_token
            if not continuation_token:
                break

        # Collect unique backup IDs from manifest files
        backup_ids: set[str] = set()
        for key in all_keys:
            if key.endswith(_MANIFEST_FILENAME):
                # Extract backup ID from "backups/{id}/manifest.json"
                parts = key[len(_BACKUP_PREFIX) :].split("/")
                if len(parts) >= 2:
                    backup_ids.add(parts[0])

        backups: list[BackupInfo] = []
        for bid in sorted(backup_ids, reverse=True):
            try:
                manifest = self._load_manifest(bid)
                backups.append(
                    BackupInfo(
                        backup_id=manifest.backup_id,
                        created_at=manifest.created_at,
                        datasets=tuple(d["name"] for d in manifest.datasets),
                        blob_prefixes=tuple(bp["prefix"] for bp in manifest.blob_prefixes),
                        total_size_bytes=manifest.total_size_bytes,
                        status=manifest.status,
                    )
                )
            except StorageError:
                _log.warning("backup_manifest_load_failed", backup_id=bid, exc_info=True)

        return backups

    def get_backup_info(self, backup_id: str) -> BackupInfo:
        """Get detailed info about a specific backup.

        Args:
            backup_id: Backup identifier.

        Returns:
            BackupInfo with backup metadata.

        Raises:
            StorageError: If backup not found.
        """
        manifest = self._load_manifest(backup_id)
        return BackupInfo(
            backup_id=manifest.backup_id,
            created_at=manifest.created_at,
            datasets=tuple(d["name"] for d in manifest.datasets),
            blob_prefixes=tuple(bp["prefix"] for bp in manifest.blob_prefixes),
            total_size_bytes=manifest.total_size_bytes,
            status=manifest.status,
        )

    def delete_backup(self, backup_id: str) -> None:
        """Delete a backup and all its data.

        Args:
            backup_id: Backup identifier.

        Raises:
            StorageError: If deletion fails.
        """
        prefix = f"{_BACKUP_PREFIX}{backup_id}/"
        count = self._blob_store.delete_prefix(prefix)
        _log.info("backup_deleted", backup_id=backup_id, objects_deleted=count)

    def verify_backup(self, backup_id: str) -> bool:
        """Verify backup integrity by checking all file checksums.

        Args:
            backup_id: Backup identifier to verify.

        Returns:
            True if all checksums match, False otherwise.

        Raises:
            StorageError: If backup manifest not found.
        """
        manifest = self._load_manifest(backup_id)
        all_ok = True

        for ds_entry in manifest.datasets:
            file_hashes = ds_entry.get("file_hashes", {})
            if not file_hashes:
                _log.debug("verify_skip_no_hashes", dataset=ds_entry["name"])
                continue

            backup_prefix = (
                f"{_BACKUP_PREFIX}{backup_id}/datasets/{ds_entry['name']}/"
            )
            for rel_path, expected_hash in file_hashes.items():
                try:
                    data = self._blob_store.download(backup_prefix + rel_path)
                    actual_hash = hashlib.sha256(data).hexdigest()
                    if actual_hash != expected_hash:
                        _log.error(
                            "verify_checksum_mismatch",
                            dataset=ds_entry["name"],
                            file=rel_path,
                            expected=expected_hash,
                            actual=actual_hash,
                        )
                        all_ok = False
                except (ImportError, OSError, RuntimeError) as exc:
                    _log.error(
                        "verify_read_failed",
                        dataset=ds_entry["name"],
                        file=rel_path,
                        error=str(exc),
                    )
                    all_ok = False

        _log.info(
            "backup_verify_complete",
            backup_id=backup_id,
            datasets=len(manifest.datasets),
            passed=all_ok,
        )
        return all_ok

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _backup_lance_dataset(self, dataset_name: str, backup_prefix: str) -> tuple[int, dict[str, str]]:
        """Copy a Lance dataset to the backup location.

        Returns (row_count, file_hashes) where file_hashes maps relative_path → sha256.
        """
        dataset_path = self._lance_base_uri / dataset_name
        if not dataset_path.is_dir():
            raise FileNotFoundError(f"Dataset not found: {dataset_path}")

        # Upload each file in the dataset directory
        row_count = 0
        file_hashes: dict[str, str] = {}
        for item in dataset_path.rglob("*"):
            if not item.is_file():
                continue
            rel_path = item.relative_to(dataset_path)
            backup_key = f"{backup_prefix}datasets/{dataset_name}/{rel_path}"

            data = item.read_bytes()
            self._blob_store.upload(backup_key, data)
            file_hashes[str(rel_path)] = hashlib.sha256(data).hexdigest()

        # Try to get row count via Lance
        try:
            import lancedb

            ds = lancedb.connect(str(dataset_path)).open_dataset(dataset_name)
            row_count = ds.count_rows()
        except (ImportError, OSError, RuntimeError):
            row_count = -1

        return row_count, file_hashes

    def _backup_blob_prefix(self, src_prefix: str, dest_prefix: str) -> int:
        """Copy all blobs from src_prefix to dest_prefix (handles pagination)."""
        count = 0
        continuation_token: str | None = None

        while True:
            result = self._blob_store.list_blobs(
                src_prefix,
                max_keys=1000,
                continuation_token=continuation_token,
            )

            for key in result.keys:
                data = self._blob_store.download(key)
                dest_key = dest_prefix + key
                self._blob_store.upload(dest_key, data)
                count += 1

            if not result.truncated:
                break
            continuation_token = result.next_token
            if not result.keys:
                break

        return count

    def _restore_lance_dataset(
        self,
        dataset_name: str,
        manifest: BackupManifest,
        *,
        overwrite: bool = False,
    ) -> None:
        """Restore a Lance dataset from backup.

        Uses atomic temp-dir + rename to avoid TOCTOU race conditions.
        """
        dest_path = self._lance_base_uri / dataset_name

        if dest_path.exists() and not overwrite:
            raise StorageError(
                error_code=ErrorCode.STORAGE_WRITE_FAILED,
                message=f"Dataset '{dataset_name}' already exists. Use overwrite=True.",
            )

        backup_prefix = f"{_BACKUP_PREFIX}{manifest.backup_id}/datasets/{dataset_name}/"

        # Get file hashes from manifest for verification
        ds_entry = next((d for d in manifest.datasets if d["name"] == dataset_name), None)
        expected_hashes: dict[str, str] = {}
        if ds_entry and "file_hashes" in ds_entry:
            expected_hashes = ds_entry["file_hashes"]

        # Restore to a temporary directory first, then atomically move.
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

                    # Verify checksum if manifest has hashes
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

            # Atomic: remove old + rename new
            if dest_path.exists():
                shutil.rmtree(dest_path)
            tmp_path.rename(dest_path)
        except StorageError:
            # Clean up temp dir on storage errors
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

    def _restore_blob_prefix(self, src_prefix: str, manifest: BackupManifest) -> None:
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

    def _load_manifest(self, backup_id: str) -> BackupManifest:
        """Load backup manifest from blob store.

        Raises:
            StorageError: If manifest not found.
        """
        key = f"{_BACKUP_PREFIX}{backup_id}/{_MANIFEST_FILENAME}"
        try:
            data = self._blob_store.download(key)
            return BackupManifest.from_json(data.decode("utf-8"))
        except StorageError as exc:
            if exc.error_code == ErrorCode.BLOB_NOT_FOUND:
                raise StorageError(
                    error_code=ErrorCode.BLOB_NOT_FOUND,
                    message=f"Backup '{backup_id}' not found",
                ) from exc
            raise

    def _estimate_backup_size(self, prefix: str) -> int:
        """Estimate total backup size by listing objects (handles pagination)."""
        total = 0
        continuation_token: str | None = None

        while True:
            result = self._blob_store.list_blobs(
                prefix,
                max_keys=5000,
                continuation_token=continuation_token,
            )

            for key in result.keys:
                try:
                    info = self._blob_store.head(key)
                    total += info.size_bytes
                except (StorageError, OSError):
                    # Best effort — skip inaccessible objects
                    pass

            if not result.truncated:
                break
            continuation_token = result.next_token
            if not result.keys:
                break

        return total

    @staticmethod
    def _generate_backup_id() -> str:
        """Generate a unique backup ID from ISO-8601 timestamp + random suffix."""
        import secrets

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        suffix = secrets.token_hex(4)  # 8 hex chars
        return f"{ts}z{suffix}"
