"""Lance storage manager -- Story 1.7, 2.1-2.6.

Provides LanceStorageManager for creating, reading, appending,
versioning, compacting, and schema migration of Lance datasets.
Integrates with MinIO via Arrow Lake config for S3-compatible storage.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa

from arrow_lake.config import StorageBackend, StorageConfig
from arrow_lake.exceptions import ErrorCode, StorageError
from arrow_lake.ingest._storage_advanced import StorageAdvancedMixin
from arrow_lake.ingest._storage_crud import StorageCRUDMixin
from arrow_lake.ingest._storage_indexing import StorageIndexingMixin
from arrow_lake.ingest._storage_versioning import StorageVersioningMixin
from arrow_lake.validation import DANGEROUS_SQL_KEYWORDS_RE, SAFE_IDENTIFIER_RE

# Backward-compatible alias for internal use
_SAFE_DATASET_NAME_RE = SAFE_IDENTIFIER_RE


@dataclass(frozen=True)
class CompactionStats:
    """Result of a dataset compaction operation."""

    version_before: int
    version_after: int
    fragments_before: int
    fragments_after: int


class LanceStorageManager(
    StorageCRUDMixin,
    StorageVersioningMixin,
    StorageIndexingMixin,
    StorageAdvancedMixin,
):
    """Manages Lance dataset storage operations.

    Supports creating, reading, appending, and versioning datasets.
    Works with local filesystem and S3-compatible storage (MinIO/AWS).

    Args:
        base_uri: Base directory for all datasets.
        storage_config: Storage configuration for S3 access (None = local).
    """

    def __init__(
        self,
        base_uri: str | Path | StorageConfig,
        *,
        storage_config: StorageConfig | None = None,
    ) -> None:
        if isinstance(base_uri, StorageConfig):
            storage_config = base_uri
            base_uri = storage_config.base_uri

        self.base_uri = str(base_uri)
        self._storage_config = storage_config
        self._storage_options = storage_config.to_storage_options() if storage_config else None
        self._connect_uri = (
            storage_config.s3_uri
            if storage_config and storage_config.backend != StorageBackend.LOCAL
            else self.base_uri
        )
        self._dataset_locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
        self._db: Any = None
        self._db_lock = threading.RLock()

    @property
    def storage_options(self) -> dict[str, str] | None:
        """Storage options for lance/boto3, or None for local filesystem."""
        return self._storage_options

    def _dataset_lock(self, name: str) -> threading.RLock:
        """Return a per-dataset reentrant lock for TOCTOU-safe write operations."""
        return self._dataset_locks[name]

    def _get_db(self):
        """Return a cached LanceDB connection (thread-safe)."""
        if self._db is None:
            with self._db_lock:
                if self._db is None:
                    import lancedb

                    self._db = lancedb.connect(
                        self._connect_uri,
                        storage_options=self._storage_options,
                    )
        return self._db

    def _get_dataset_path(self, name: str) -> str:
        """Get the logical path for a named dataset (no .lance suffix).

        Used by lancedb operations (create/open) which manage
        the .lance suffix internally.
        """
        return str(Path(self.base_uri) / name)

    def _lance_dir(self, name: str) -> Path:
        """Get the filesystem directory for a named dataset (.lance suffix).

        Used for filesystem-level operations (exists, delete, list).
        lancedb creates {name}.lance directories on disk.
        """
        return Path(self.base_uri) / f"{name}.lance"

    def dataset_uri(self, name: str) -> str:
        """Get the Lance dataset URI for a named dataset.

        Returns the filesystem path to the .lance directory, suitable for
        use with __lance_scan() or lance.dataset().

        Args:
            name: Dataset name.

        Returns:
            Absolute path string to the Lance dataset directory.
            For S3 backends, returns an s3:// URI.
        """
        self._validate_name(name)
        if self._storage_config and self._storage_config.backend != StorageBackend.LOCAL:
            path = self._storage_config.base_uri
            if path.startswith("./"):
                path = path[2:]
            return f"{self._storage_config.s3_uri.rstrip('/')}/{name}.lance"
        return str(self._lance_dir(name))

    @staticmethod
    def _validate_name(name: str) -> None:
        """Validate dataset name against safe identifier pattern."""
        if not _SAFE_DATASET_NAME_RE.match(name):
            raise StorageError(
                error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
                message=(f"Invalid dataset name '{name}': must match ^[a-zA-Z_][a-zA-Z0-9_-]*$"),
            )

    @staticmethod
    def _validate_identifier(value: str, label: str = "identifier") -> None:
        """Validate a tag or column name against safe identifier pattern."""
        if not _SAFE_DATASET_NAME_RE.match(value):
            raise StorageError(
                error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
                message=f"Invalid {label} '{value}': must match ^[a-zA-Z_][a-zA-Z0-9_-]*$",
            )

    @staticmethod
    def _validate_sql_expr(expr: str) -> None:
        """Validate a SQL expression for dangerous keywords and semicolons."""
        if DANGEROUS_SQL_KEYWORDS_RE.search(expr):
            raise StorageError(
                error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
                message=f"Dangerous SQL keyword detected in expression: {expr}",
            )
        if ";" in expr:
            raise StorageError(
                error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
                message=f"Semicolons not allowed in SQL expression: {expr}",
            )

    def _write_lance(self, data: pa.Table, path: str, mode: str = "create") -> None:
        """Write data to Lance format via lancedb."""
        db = self._get_db()

        if mode == "create":
            db.create_table(Path(path).name, data)
        elif mode == "append":
            table = db.open_table(Path(path).name)
            table.add(data)

    def _open_lance(self, path: str) -> Any:
        """Open a Lance dataset via lancedb (latest version only).

        For version-specific reads, use lance.dataset() directly
        in read_dataset() / read_at_tag().

        Args:
            path: Dataset path.

        Returns:
            Lance dataset object.

        Raises:
            StorageError: If dataset cannot be opened.
        """
        import lancedb

        name = Path(path).stem
        if not self.dataset_exists(name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' not found",
            )

        db = self._get_db()
        return db.open_table(name)

    def open_dataset(self, dataset_name: str) -> Any:
        """Open a Lance dataset by name via lancedb.

        Public wrapper around _open_lance for use by query bridges.

        Args:
            dataset_name: Dataset name.

        Returns:
            LanceDB Table object.

        Raises:
            StorageError: If dataset cannot be opened.
        """
        return self._open_lance(self._get_dataset_path(dataset_name))

    def open_dataset_versioned(self, dataset_name: str, version: int) -> Any:
        """Open a Lance dataset at a specific version.

        Returns a lance.LanceDataset (not lancedb.Table) for versioned
        search operations. The returned object supports .search() and
        .to_arrow() like lancedb.Table.

        Args:
            dataset_name: Dataset name.
            version: Lance dataset version number.

        Returns:
            lance.LanceDataset at the specified version.

        Raises:
            StorageError: If dataset or version not found.
        """
        import lance

        lance_uri = self.dataset_uri(dataset_name)
        try:
            return lance.dataset(lance_uri, version=version, storage_options=self._storage_options)
        except (ValueError, OSError) as exc:
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Version {version} not found for dataset '{dataset_name}'",
            ) from exc


__all__ = ["CompactionStats", "LanceStorageManager"]
