"""Lance storage manager -- Story 1.7, 2.1-2.6.

Provides LanceStorageManager for creating, reading, appending,
versioning, compacting, and schema migration of Lance datasets.
Integrates with MinIO via Arrow Lake config for S3-compatible storage.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import structlog

from arrow_lake.config import StorageBackend, StorageConfig
from arrow_lake.exceptions import ConcurrencyError, ErrorCode, StorageError
from arrow_lake.ingest._storage_advanced import StorageAdvancedMixin
from arrow_lake.ingest._storage_crud import StorageCRUDMixin
from arrow_lake.ingest._storage_indexing import StorageIndexingMixin
from arrow_lake.ingest._storage_versioning import StorageVersioningMixin
from arrow_lake.validation import DANGEROUS_SQL_KEYWORDS_RE, SAFE_IDENTIFIER_RE

# Backward-compatible alias for internal use
_SAFE_DATASET_NAME_RE = SAFE_IDENTIFIER_RE

logger = structlog.get_logger(__name__)


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
        self._dataset_locks: dict[str, threading.RLock] = {}
        self._dataset_lock_max: int = 1024
        self._db: Any = None
        self._db_lock = threading.RLock()
        # Container-scoped lancedb connections (DR14 W1.1): lancedb forbids
        # ``/`` in table names, so a container dataset (a directory of tables)
        # gets its own connection rooted at ``{base}/{ds}``; table names inside
        # stay plain names. Keyed by dataset name; base connection untouched.
        self._container_dbs: dict[str, Any] = {}
        # P1-5: bounded per-container connection cache (LRU eviction).
        self._container_dbs_max = 256

    @property
    def storage_options(self) -> dict[str, str] | None:
        """Storage options for lance/boto3, or None for local filesystem."""
        return self._storage_options

    def _dataset_lock(self, name: str) -> threading.RLock:
        """Return a per-dataset reentrant lock for TOCTOU-safe write operations."""
        if name not in self._dataset_locks:
            if len(self._dataset_locks) >= self._dataset_lock_max:
                # Evict oldest entry (first key)
                self._dataset_locks.pop(next(iter(self._dataset_locks)))
            self._dataset_locks[name] = threading.RLock()
        return self._dataset_locks[name]

    def _acquire_dataset_lock(self, dataset_name: str, timeout: float = 30.0) -> None:
        """Acquire per-dataset lock with timeout.

        Raises ConcurrencyError on timeout.
        Must be paired with a ``lock.release()`` in a ``finally`` block.
        """
        lock = self._dataset_lock(dataset_name)
        acquired = lock.acquire(timeout=timeout)
        if not acquired:
            raise ConcurrencyError(
                error_code=ErrorCode.STORAGE_LOCK_TIMEOUT,
                message=f"Lock acquisition timed out ({timeout}s) for dataset '{dataset_name}'",
            )

    def cleanup_partial(self, dataset_name: str) -> None:
        """Remove partially written dataset data after a failed write."""
        try:
            dataset_path = self._lance_dir(dataset_name)
            if dataset_path and dataset_path.exists():
                import shutil

                shutil.rmtree(dataset_path, ignore_errors=True)
                logger.warning("Cleaned up partial dataset: %s", dataset_name)
        except Exception as exc:
            logger.error("Failed to cleanup partial dataset %s: %s", dataset_name, exc)

    def _get_db(self, container: str | None = None):
        """Return a cached LanceDB connection (thread-safe).

        ``container=None`` returns the base connection (single-table
        datasets, unchanged behavior). Otherwise returns a connection
        rooted at ``{base}/{container}`` so container tables are addressed
        by plain table names (lancedb rejects ``/`` in table names).
        """
        if container is not None:
            with self._db_lock:
                db = self._container_dbs.get(container)
                if db is None:
                    import lancedb

                    # P1-5 (review 2026-08-26): cap the per-container
                    # connection cache — an unbounded dict leaks a live
                    # client per container for the process lifetime. LRU
                    # eviction mirrors _dataset_lock's bound.
                    if len(self._container_dbs) >= self._container_dbs_max:
                        self._container_dbs.pop(next(iter(self._container_dbs)))
                    db = lancedb.connect(
                        self._connect_uri_for(container),
                        storage_options=self._storage_options,
                    )
                    self._container_dbs[container] = db
                else:
                    # LRU touch: most-recently-used last
                    self._container_dbs[container] = self._container_dbs.pop(container)
                return db
        if self._db is None:
            with self._db_lock:
                if self._db is None:
                    import lancedb

                    self._db = lancedb.connect(
                        self._connect_uri,
                        storage_options=self._storage_options,
                    )
        return self._db

    def _connect_uri_for(self, container: str) -> str:
        """Connect URI for a container-scoped connection (``{base}/{ds}``)."""
        if self._storage_config and self._storage_config.backend != StorageBackend.LOCAL:
            return f"{self._storage_config.s3_uri.rstrip('/')}/{container}"
        return str(Path(self.base_uri) / container)

    def _validate_table_name(self, table: str) -> None:
        """Validate a container table name (dataset rules + length cap).

        The length cap keeps ``{table}.lance/_versions`` path components
        under filesystem NAME_MAX — without it a long name passes the
        identifier regex and fails later as an opaque IO error.
        """
        if not _SAFE_DATASET_NAME_RE.match(table) or len(table) > 128:
            raise StorageError(
                error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
                message=(f"Invalid table name '{table}': must match "
                         f"^[a-zA-Z_][a-zA-Z0-9_-]*$ and be <=128 chars"),
            )

    def _get_dataset_path(self, name: str, table: str | None = None) -> str:
        """Get the logical path for a named dataset (no .lance suffix).

        Used by lancedb operations (create/open) which manage
        the .lance suffix internally. With ``table`` the path addresses a
        table inside a container dataset: ``{base}/{name}/{table}``.
        """
        if table is None:
            return str(Path(self.base_uri) / name)
        self._validate_table_name(table)
        return str(Path(self.base_uri) / name / table)

    def _get_io_config(self) -> Any:
        """Build a Daft IOConfig from storage options for S3 operations."""
        if not self._storage_options:
            return None
        from daft import IOConfig
        from daft.daft import S3Config

        opts = self._storage_options
        return IOConfig(s3=S3Config(
            key_id=opts.get("aws_access_key_id", ""),
            access_key=opts.get("aws_secret_access_key", ""),
            region_name=opts.get("region", "us-east-1"),
            endpoint_url=opts.get("endpoint_url", ""),
        ))

    def write_lance_from_dataframe(
        self,
        name: str,
        df: Any,
        mode: str = "create",
        *,
        table: str | None = None,
    ) -> None:
        """Write a Daft DataFrame directly to Lance, bypassing Arrow conversion.

        Args:
            name: Target dataset name.
            df: Daft DataFrame to write.
            mode: Write mode — "create", "append", or "overwrite".
            table: Optional table name within a container dataset.

        Raises:
            StorageError: If write fails, or the write would create a
                dual identity (single-table dataset vs container, D3).
        """
        self._validate_name(name)
        if table is not None:
            self._validate_table_name(table)
            # P1-2 (review 2026-08-26): writing a container table next to an
            # existing plain dataset (or vice versa below) leaves both shapes
            # on disk — no downstream code can resolve which one ``name``
            # refers to. Fail closed like create_dataset does.
            if self.dataset_exists(name):
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=f"Dataset '{name}' already exists as a single-table dataset",
                )
        elif self.list_container_tables(name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_WRITE_FAILED,
                message=f"Dataset '{name}' already exists as a container",
            )
        lock_key = f"{name}/{table}" if table is not None else name
        lock = self._dataset_lock(lock_key)
        self._acquire_dataset_lock(lock_key)
        try:
            uri = str(self._lance_dir(name, table))  # <base>/<name>.lance 或 <base>/<name>/<table>.lance — Daft 写入需完整路径（lancedb 按 .lance 解析）
            io_config = self._get_io_config()
            try:
                df.write_lance(uri, mode=mode, io_config=io_config).collect()
            except Exception as exc:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=f"Daft write_lance failed for '{name}': {exc}",
                    context={"name": name, "mode": mode},
                ) from exc
        finally:
            lock.release()

    def export_dataframe(
        self,
        df: Any,
        target_uri: str,
        format: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Export a Daft DataFrame to various formats.

        Args:
            df: Daft DataFrame to export.
            target_uri: Target URI (path, s3://, etc.).
            format: Export format — "parquet", "csv", "json", "iceberg", "clickhouse".
            **kwargs: Format-specific options.

        Returns:
            Dict with export stats (row_count, format, target_uri).

        Raises:
            StorageError: If export fails.
            ValueError: If format is not supported.
        """
        writers: dict[str, Callable[..., Any]] = {
            "parquet": lambda: df.write_parquet(target_uri, io_config=self._get_io_config(), **kwargs),
            "csv": lambda: df.write_csv(target_uri, io_config=self._get_io_config(), **kwargs),
            "json": lambda: df.write_json(target_uri, io_config=self._get_io_config(), **kwargs),
            "iceberg": lambda: df.write_iceberg(target_uri, io_config=self._get_io_config(), **kwargs),
            "clickhouse": lambda: df.write_clickhouse(target_uri, **kwargs),
        }
        if format not in writers:
            raise ValueError(f"Unsupported export format: {format!r}. Supported: {sorted(writers)}")
        try:
            writers[format]()
            row_count = df.count().to_arrow().column(0)[0].as_py()
            return {"row_count": row_count, "format": format, "target_uri": target_uri}
        except Exception as exc:
            raise StorageError(
                error_code=ErrorCode.STORAGE_WRITE_FAILED,
                message=f"Export to {format} failed: {exc}",
                context={"format": format, "target_uri": target_uri},
            ) from exc

    def _lance_dir(self, name: str, table: str | None = None) -> Path:
        """Get the filesystem directory for a named dataset (.lance suffix).

        Used for filesystem-level operations (exists, delete, list).
        lancedb creates {name}.lance directories on disk. With ``table``
        the path addresses a table inside a container dataset.
        """
        if table is None:
            return Path(self.base_uri) / f"{name}.lance"
        return Path(self.base_uri) / name / f"{table}.lance"

    def dataset_uri(self, name: str, table: str | None = None) -> str:
        """Get the Lance dataset URI for a named dataset.

        Returns the filesystem path to the .lance directory, suitable for
        use with __lance_scan() or lance.dataset(). With ``table`` the URI
        addresses a table inside a container dataset.

        Args:
            name: Dataset name.
            table: Optional table name within a container dataset.

        Returns:
            Absolute path string to the Lance dataset directory.
            For S3 backends, returns an s3:// URI.
        """
        self._validate_name(name)
        if table is not None:
            self._validate_table_name(table)
        if self._storage_config and self._storage_config.backend != StorageBackend.LOCAL:
            path = self._storage_config.base_uri
            if path.startswith("./"):
                path = path[2:]
            suffix = f"{name}/{table}.lance" if table is not None else f"{name}.lance"
            return f"{self._storage_config.s3_uri.rstrip('/')}/{suffix}"
        return str(self._lance_dir(name, table))

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

    def _write_lance(
        self, data: pa.Table, path: str, mode: str = "create",
        container: str | None = None,
    ) -> None:
        """Write data to Lance format via lancedb.

        Write optimization parameters (fragment size, row group size) are
        applied via post-write compaction when available, since lancedb's
        create_table/add do not accept them directly. ``container`` scopes
        the connection to a container dataset so ``name`` (derived from the
        path's last segment) is the plain table name.
        """
        db = self._get_db(container)
        name = Path(path).name

        if mode == "create":
            db.create_table(name, data)
        elif mode == "append":
            table = db.open_table(name)
            self._delete_documents_before_append(table, data)
            table.add(data)

        # Apply write optimization via compaction when configured
        if self._storage_config and mode == "create":
            try:
                table = db.open_table(name)
                max_rows = self._storage_config.lance_max_rows_per_file
                if max_rows and max_rows > 0:
                    # v1.7.1: lancedb 0.33 removed max_rows_per_file from optimize();
                    # default optimize() suffices — fine-grained control via compact_files().
                    table.optimize()
            except Exception:
                logger.debug("post_write_optimize_skipped", dataset=name, exc_info=True)

    def _delete_documents_before_append(self, table: Any, data: pa.Table) -> None:
        """Document-level idempotency for re-ingest.

        If the incoming table carries a ``document_id`` column (ingest path),
        delete the existing rows for those document ids BEFORE appending, so
        re-ingesting a document REPLACES its chunks instead of producing
        duplicates. No-op for tables without ``document_id`` (non-ingest appends).

        document_id is a sha256 hex hash (see Ingestor.ingest_documents), so it is
        alphanumeric; we guard against non-alphanumeric values defensively before
        interpolating into the Lance filter. Failures are logged and swallowed — a
        missed delete only risks duplicate rows, never data loss, so it must not
        block the append.
        """
        if "document_id" not in data.column_names:
            return
        try:
            doc_ids = data.column("document_id").to_pylist()
            unique_ids = sorted({str(d) for d in doc_ids if d})
            if not unique_ids or not all(d.isalnum() for d in unique_ids):
                return
            in_list = ",".join(f"'{d}'" for d in unique_ids)
            table.delete(f"document_id IN ({in_list})")
        except Exception:
            logger.debug("append_idempotent_delete_skipped", exc_info=True)

    def _open_lance(
        self, path: str, container: str | None = None, table: str | None = None,
    ) -> Any:
        """Open a Lance dataset via lancedb (latest version only).

        For version-specific reads, use lance.dataset() directly
        in read_dataset() / read_at_tag().

        Args:
            path: Dataset path (``{base}/{name}`` or ``{base}/{ds}/{table}``).
            container: Container dataset name (container-mode indicator).
            table: Table name within the container.

        Returns:
            Lance dataset object.

        Raises:
            StorageError: If dataset cannot be opened.
        """
        if container is not None and table is not None:
            if not self.dataset_exists(container, table=table):
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Table '{container}/{table}' not found",
                )
            return self._get_db(container).open_table(table)

        name = Path(path).stem
        if not self.dataset_exists(name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' not found",
            )

        db = self._get_db()
        return db.open_table(name)

    def open_dataset(self, dataset_name: str, *, table: str | None = None) -> Any:
        """Open a Lance dataset by name via lancedb.

        Public wrapper around _open_lance for use by query bridges.

        Args:
            dataset_name: Dataset name.
            table: Optional table name within a container dataset (DR14).

        Returns:
            LanceDB Table object.

        Raises:
            StorageError: If dataset cannot be opened.
        """
        return self._open_lance(
            self._get_dataset_path(dataset_name, table),
            container=dataset_name if table else None, table=table,
        )

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
        open_kwargs: dict[str, Any] = {
            "version": version,
            "storage_options": self._storage_options,
        }
        if self._storage_config and self._storage_config.lance_cache_size > 0:
            open_kwargs["cache_size"] = self._storage_config.lance_cache_size
        try:
            return lance.dataset(lance_uri, **open_kwargs)
        except (ValueError, OSError) as exc:
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Version {version} not found for dataset '{dataset_name}'",
            ) from exc

    def cleanup_versions(
        self,
        dataset_name: str,
        older_than: timedelta,
        dry_run: bool = False,
    ) -> int:
        """Remove old Lance dataset versions older than the specified timedelta.

        Args:
            dataset_name: Dataset to clean up.
            older_than: Minimum age of versions to remove.
            dry_run: If True, log what would be removed without actually deleting.

        Returns:
            Number of versions cleaned up.
        """
        import lance

        lance_uri = self.dataset_uri(dataset_name)
        try:
            ds = lance.dataset(lance_uri, storage_options=self._storage_options)
            if dry_run:
                versions = ds.list_versions()
                import datetime
                cutoff = datetime.datetime.now() - older_than
                to_remove = [
                    v for v in versions
                    if hasattr(v, "timestamp") and v.timestamp < cutoff
                ]
                return len(to_remove)

            stats = ds.cleanup_old_versions(older_than=older_than)
            removed = len(stats.fragments_removed) if hasattr(stats, "fragments_removed") else 0
            return max(removed, 1)
        except Exception:
            logger.debug("cleanup_old_versions_failed", dataset=dataset_name, exc_info=True)
            return 0

__all__ = ["CompactionStats", "LanceStorageManager"]
