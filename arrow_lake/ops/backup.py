"""Backup and restore operations — Story M1.

Provides BackupManager for full dataset backups (Lance data + metadata),
blob store backup (MinIO/S3 prefix snapshots), manifest-based restore,
and backup listing, info, cleanup, and verification.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from arrow_lake.config import StorageBackend, StorageConfig
from arrow_lake.exceptions import BackupError, ErrorCode, StorageError
from arrow_lake.ops.backup_restore import BackupRestorer
from arrow_lake.storage.blob_store import BlobStoreManager

_log = structlog.get_logger(__name__)

__all__ = ["BackupInfo", "BackupManager", "BackupManifest", "BackupRestorer"]

_BACKUP_PREFIX = "backups/"
_MANIFEST_FILENAME = "manifest.json"
_METADATA_FILENAME = "_backup_meta.json"


@dataclass(frozen=True)
class BackupInfo:
    backup_id: str
    created_at: str
    datasets: tuple[str, ...]
    blob_prefixes: tuple[str, ...]
    total_size_bytes: int
    status: str


@dataclass
class BackupManifest:
    backup_id: str
    created_at: str
    datasets: list[dict[str, Any]] = field(default_factory=list)
    blob_prefixes: list[dict[str, Any]] = field(default_factory=list)
    lance_base_uri: str = ""
    total_size_bytes: int = 0
    status: str = "complete"

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> BackupManifest:
        data = json.loads(raw)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class BackupManager:
    """Manages backup and restore for Arrow Lake datasets and blob prefixes."""

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

        Returns BackupInfo. Sets manifest.status='partial' if any item fails.
        """
        backup_id = backup_id or self._generate_backup_id()
        prefix = f"{_BACKUP_PREFIX}{backup_id}/"
        timestamp = datetime.now(UTC).isoformat()
        manifest = BackupManifest(
            backup_id=backup_id,
            created_at=timestamp,
            lance_base_uri=str(self._lance_base_uri),
        )

        if dataset_names is not None:
            for name in dataset_names:
                try:
                    rows, file_hashes = self._backup_lance_dataset(name, prefix)
                    manifest.datasets.append({"name": name, "rows": rows, "file_hashes": file_hashes})
                except FileNotFoundError as exc:
                    _log.warning("backup_dataset_not_found", dataset=name, backup_id=backup_id, error=str(exc))
                    manifest.status = "partial"
                except StorageError:
                    _log.warning("backup_dataset_storage_error", dataset=name, backup_id=backup_id, exc_info=True)
                    manifest.status = "partial"
                except (OSError, RuntimeError) as exc:
                    _log.error("backup_dataset_unexpected_error", dataset=name, backup_id=backup_id, error=str(exc), exc_info=True)
                    manifest.status = "partial"

        if blob_prefixes:
            for bp in blob_prefixes:
                try:
                    count = self._backup_blob_prefix(bp, prefix + "blobs/")
                    manifest.blob_prefixes.append({"prefix": bp, "object_count": count})
                except StorageError:
                    _log.warning("backup_blob_prefix_storage_error", prefix=bp, backup_id=backup_id, exc_info=True)
                    manifest.status = "partial"
                except Exception as exc:
                    _log.error("backup_blob_prefix_unexpected_error", prefix=bp, backup_id=backup_id, error=str(exc), exc_info=True)
                    manifest.status = "partial"

        manifest.total_size_bytes = self._estimate_backup_size(prefix)

        # Upload manifest atomically (staging → copy → delete staging)
        manifest_bytes = manifest.to_json().encode("utf-8")
        staging_key = f"{prefix}{_MANIFEST_FILENAME}.staging"
        final_key = f"{prefix}{_MANIFEST_FILENAME}"
        try:
            self._blob_store.upload(staging_key, manifest_bytes, content_type="application/json")
            self._blob_store.copy(staging_key, final_key)
        finally:
            with contextlib.suppress(StorageError, OSError):
                self._blob_store.delete(staging_key)

        _log.info(
            "backup_created",
            backup_id=backup_id,
            datasets=len(manifest.datasets),
            blob_prefixes=len(manifest.blob_prefixes),
            total_size_bytes=manifest.total_size_bytes,
            status=manifest.status,
        )
        return self._manifest_to_info(manifest)

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
        """Restore a backup. Raises BackupError if restore fails."""
        manifest = self._load_manifest(backup_id)
        restorer = self._get_restorer()

        if dataset_names is None:
            dataset_names = [d["name"] for d in manifest.datasets]
        if blob_prefixes is None:
            blob_prefixes = [bp["prefix"] for bp in manifest.blob_prefixes]

        for name in dataset_names:
            self._restore_item(restorer.restore_lance_dataset, name, manifest, backup_id, "dataset", overwrite=overwrite)

        for bp in blob_prefixes:
            self._restore_item(restorer.restore_blob_prefix, bp, manifest, backup_id, "blob_prefix")

        _log.info("backup_restored", backup_id=backup_id, datasets=dataset_names, blob_prefixes=blob_prefixes)
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
        """List all available backups by paginating the backup prefix."""
        all_keys = self._paginate_keys(_BACKUP_PREFIX)

        backup_ids: set[str] = set()
        for key in all_keys:
            if key.endswith(_MANIFEST_FILENAME):
                parts = key[len(_BACKUP_PREFIX) :].split("/")
                if len(parts) >= 2:
                    backup_ids.add(parts[0])

        backups: list[BackupInfo] = []
        for bid in sorted(backup_ids, reverse=True):
            try:
                manifest = self._load_manifest(bid)
                backups.append(self._manifest_to_info(manifest))
            except StorageError:
                _log.warning("backup_manifest_load_failed", backup_id=bid, exc_info=True)
        return backups

    def get_backup_info(self, backup_id: str) -> BackupInfo:
        """Get info about a specific backup. Raises StorageError if not found."""
        return self._manifest_to_info(self._load_manifest(backup_id))

    def delete_backup(self, backup_id: str) -> None:
        """Delete a backup and all its data."""
        prefix = f"{_BACKUP_PREFIX}{backup_id}/"
        count = self._blob_store.delete_prefix(prefix)
        _log.info("backup_deleted", backup_id=backup_id, objects_deleted=count)

    def verify_backup(self, backup_id: str) -> bool:
        """Verify backup integrity via SHA-256 checksums. Returns True if all match."""
        manifest = self._load_manifest(backup_id)
        all_ok = True

        for ds_entry in manifest.datasets:
            file_hashes = ds_entry.get("file_hashes", {})
            if not file_hashes:
                continue
            backup_prefix = f"{_BACKUP_PREFIX}{backup_id}/datasets/{ds_entry['name']}/"
            for rel_path, expected_hash in file_hashes.items():
                try:
                    data = self._blob_store.download(backup_prefix + rel_path)
                    actual_hash = hashlib.sha256(data).hexdigest()
                    if actual_hash != expected_hash:
                        _log.error("verify_checksum_mismatch", dataset=ds_entry["name"], file=rel_path,
                                   expected=expected_hash, actual=actual_hash)
                        all_ok = False
                except (ImportError, OSError, RuntimeError) as exc:
                    _log.error("verify_read_failed", dataset=ds_entry["name"], file=rel_path, error=str(exc))
                    all_ok = False

        _log.info("backup_verify_complete", backup_id=backup_id, passed=all_ok)
        return all_ok

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _manifest_to_info(manifest: BackupManifest) -> BackupInfo:
        return BackupInfo(
            backup_id=manifest.backup_id,
            created_at=manifest.created_at,
            datasets=tuple(d["name"] for d in manifest.datasets),
            blob_prefixes=tuple(bp["prefix"] for bp in manifest.blob_prefixes),
            total_size_bytes=manifest.total_size_bytes,
            status=manifest.status,
        )

    def _restore_item(self, restore_fn: Any, item_name: str, manifest: BackupManifest,
                      backup_id: str, item_type: str, **kwargs: Any) -> None:
        """Restore a single item (dataset or blob prefix) with error handling."""
        try:
            restore_fn(item_name, manifest, **kwargs)
        except StorageError:
            raise
        except (OSError, shutil.Error) as exc:
            _log.error(f"restore_{item_type}_failed", item=item_name, backup_id=backup_id, error=str(exc))
            raise BackupError(
                error_code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Failed to restore {item_type} '{item_name}' from backup '{backup_id}': {exc}",
            ) from exc

    def _paginate_keys(self, prefix: str) -> list[str]:
        """List all blob keys under a prefix (handles pagination)."""
        all_keys: list[str] = []
        continuation_token: str | None = None
        while True:
            result = self._blob_store.list_blobs(prefix, continuation_token=continuation_token)
            all_keys.extend(result.keys)
            continuation_token = result.next_token
            if not continuation_token:
                break
        return all_keys

    def _backup_lance_dataset(self, dataset_name: str, backup_prefix: str) -> tuple[int, dict[str, str]]:
        """Copy a Lance dataset to the backup location. Returns (row_count, file_hashes)."""
        if ".." in dataset_name or "/" in dataset_name or "\\" in dataset_name:
            raise ValueError(f"Invalid dataset name (path traversal): {dataset_name!r}")

        if self._storage_config.backend != StorageBackend.LOCAL:
            return self._backup_lance_dataset_remote(dataset_name, backup_prefix)

        # --- Local filesystem ---
        resolved = (self._lance_base_uri / dataset_name).resolve()
        base = self._lance_base_uri.resolve()
        if not str(resolved).startswith(str(base)):
            raise ValueError(f"Invalid dataset name (path traversal): {dataset_name!r}")
        dataset_path = self._lance_base_uri / dataset_name
        if not dataset_path.is_dir():
            raise FileNotFoundError(f"Dataset not found: {dataset_path}")

        row_count = 0
        file_hashes: dict[str, str] = {}
        for item in dataset_path.rglob("*"):
            if not item.is_file():
                continue
            rel_path = item.relative_to(dataset_path)
            backup_key = f"{backup_prefix}datasets/{dataset_name}/{rel_path}"
            file_size = item.stat().st_size
            sha = hashlib.sha256()
            _chunk_size = 8 * 1024 * 1024
            if file_size <= _chunk_size:
                data = item.read_bytes()
                sha.update(data)
                self._blob_store.upload(backup_key, data)
            else:
                chunks: list[bytes] = []
                with open(item, "rb") as f:
                    while True:
                        chunk = f.read(_chunk_size)
                        if not chunk:
                            break
                        sha.update(chunk)
                        chunks.append(chunk)
                self._blob_store.upload(backup_key, b"".join(chunks))
            file_hashes[str(rel_path)] = sha.hexdigest()

        try:
            import lancedb
            conn = lancedb.connect(str(self._lance_base_uri))
            ds = conn.open_table(dataset_name)
            row_count = ds.count_rows()
        except (ImportError, OSError, RuntimeError, AttributeError, ValueError):
            row_count = -1

        return row_count, file_hashes

    def _s3_dataset_prefix(self, dataset_name: str) -> str:
        """Compute the S3 key prefix for a Lance dataset."""
        base = self._storage_config.base_uri
        if base.startswith("./"):
            base = base[2:]
        return f"{base}/{dataset_name}.lance/"

    def _backup_lance_dataset_remote(
        self, dataset_name: str, backup_prefix: str,
    ) -> tuple[int, dict[str, str]]:
        """Copy a Lance dataset from remote (S3/MinIO) to backup via server-side copy.

        Container datasets (DR14 W4.4): a container's tables live under
        ``{base}/{name}/{table}.lance`` — when the plain ``{name}.lance/``
        prefix has no objects, the ``{name}/`` prefix is retried and its
        contents copied recursively (all tables in one manifest entry).
        """
        row_count = -1
        file_hashes: dict[str, str] = {}
        found_any = False
        base = self._storage_config.base_uri
        if base.startswith("./"):
            base = base[2:]

        for s3_prefix in (f"{base}/{dataset_name}.lance/", f"{base}/{dataset_name}/"):
            continuation_token: str | None = None
            prefix_found = False
            while True:
                result = self._blob_store.list_blobs(
                    s3_prefix, max_keys=5000, continuation_token=continuation_token,
                )
                for key in result.keys:
                    prefix_found = True
                    found_any = True
                    rel_path = key[len(s3_prefix):]
                    backup_key = f"{backup_prefix}datasets/{dataset_name}/{rel_path}"
                    self._blob_store.copy(key, backup_key)
                    # Record etag+size for integrity verification without downloading
                    try:
                        info = self._blob_store.head(backup_key)
                        file_hashes[rel_path] = f"{info.etag}:{info.size_bytes}"
                    except Exception:
                        file_hashes[rel_path] = "copy-ok"
                if not result.truncated:
                    break
                continuation_token = result.next_token
                if not result.keys:
                    break
            if prefix_found:
                break  # plain dataset hit — skip the container fallback

        if not found_any:
            raise FileNotFoundError(
                f"Dataset not found in S3: {base}/{dataset_name}[.lance]/"
            )

        try:
            import lancedb
            uri = self._storage_config.s3_uri
            opts = self._storage_config.to_storage_options()
            db = lancedb.connect(uri, storage_options=opts)
            table = db.open_table(dataset_name)
            row_count = table.count_rows()
        except (ImportError, OSError, RuntimeError):
            pass

        return row_count, file_hashes

    def _backup_blob_prefix(self, src_prefix: str, dest_prefix: str) -> int:
        """Copy all blobs from src_prefix to dest_prefix via server-side copy."""
        count = 0
        continuation_token: str | None = None
        while True:
            result = self._blob_store.list_blobs(src_prefix, max_keys=1000, continuation_token=continuation_token)
            for key in result.keys:
                self._blob_store.copy(key, dest_prefix + key)
                count += 1
            if not result.truncated or not result.keys:
                break
            continuation_token = result.next_token
        return count

    def _get_restorer(self) -> BackupRestorer:
        return BackupRestorer(
            blob_store=self._blob_store,
            lance_base_uri=str(self._lance_base_uri),
            storage_config=self._storage_config,
        )

    def _load_manifest(self, backup_id: str) -> BackupManifest:
        """Load backup manifest. Raises StorageError if not found."""
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
            result = self._blob_store.list_blobs(prefix, max_keys=5000, continuation_token=continuation_token)
            for key in result.keys:
                with contextlib.suppress(StorageError, OSError):
                    total += self._blob_store.head(key).size_bytes
            if not result.truncated or not result.keys:
                break
            continuation_token = result.next_token
        return total

    @staticmethod
    def _generate_backup_id() -> str:
        import secrets
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        return f"{ts}z{secrets.token_hex(4)}"
