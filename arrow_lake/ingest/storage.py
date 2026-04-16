"""Lance storage manager — Story 1.7, 2.1-2.6.

Provides LanceStorageManager for creating, reading, appending,
versioning, compacting, and schema migration of Lance datasets.
Integrates with MinIO via Arrow Lake config for S3-compatible storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pyarrow as pa

from arrow_lake.exceptions import ErrorCode, StorageError
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


class LanceStorageManager:
    """Manages Lance dataset storage operations.

    Supports creating, reading, appending, and versioning datasets.
    Works with local filesystem and S3-compatible storage (MinIO/AWS).

    Args:
        base_uri: Base directory for all datasets.
    """

    def __init__(self, base_uri: str | Path) -> None:
        self.base_uri = str(base_uri)

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

    def create_dataset(self, name: str, data: pa.Table) -> None:
        """Create a new Lance dataset.

        Args:
            name: Dataset name.
            data: Arrow table to write.

        Raises:
            StorageError: If dataset already exists or name is invalid.
        """
        self._validate_name(name)
        if self.dataset_exists(name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_WRITE_FAILED,
                message=f"Dataset '{name}' already exists",
            )
        path = self._get_dataset_path(name)
        self._write_lance(data, path, mode="create")

    def read_dataset(
        self, name: str, version: int | None = None, columns: list[str] | None = None
    ) -> pa.Table:
        """Read a Lance dataset.

        Args:
            name: Dataset name.
            version: Specific version to read (None = latest).
            columns: Optional column subset to read.

        Returns:
            Arrow Table with the dataset contents.

        Raises:
            StorageError: If dataset does not exist, name is invalid, or version is invalid.
        """
        self._validate_name(name)
        lance_dir = self._lance_dir(name)

        if not lance_dir.is_dir():
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' not found",
            )

        if version is not None:
            import lance

            try:
                ds = lance.dataset(str(lance_dir), version=version)
            except (ValueError, OSError) as exc:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Version {version} not found for dataset '{name}'",
                ) from exc

            if columns is not None:
                return ds.to_table(columns=columns)
            return ds.to_table()

        table = self._open_lance(self._get_dataset_path(name))

        if columns is not None:
            return table.search().select(columns).to_arrow()
        return table.search().to_arrow()

    def append_dataset(self, name: str, data: pa.Table) -> None:
        """Append data to an existing Lance dataset.

        Args:
            name: Dataset name.
            data: Arrow table to append.

        Raises:
            StorageError: If dataset does not exist or name is invalid.
        """
        self._validate_name(name)
        path = self._get_dataset_path(name)
        if not self.dataset_exists(name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' does not exist, cannot append",
            )
        self._write_lance(data, path, mode="append")

    def delete_dataset(self, name: str) -> None:
        """Delete a Lance dataset.

        Args:
            name: Dataset name.

        Raises:
            StorageError: If dataset does not exist or name is invalid.
        """
        self._validate_name(name)
        import shutil

        path = self._lance_dir(name)
        if not path.is_dir():
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' does not exist at {path}",
            )
        shutil.rmtree(path)

    def dataset_exists(self, name: str) -> bool:
        """Check if a dataset exists.

        Args:
            name: Dataset name.

        Returns:
            True if the dataset directory exists.
        """
        self._validate_name(name)
        return self._lance_dir(name).is_dir()

    def list_datasets(self) -> list[str]:
        """List all dataset names.

        Returns:
            Sorted list of dataset names (without .lance suffix).
        """
        base = Path(self.base_uri)
        if not base.exists():
            return []
        return sorted(p.stem for p in base.iterdir() if p.is_dir() and p.name.endswith(".lance"))

    def compact(self, name: str) -> CompactionStats:
        """Compact a dataset by merging small fragment files.

        Args:
            name: Dataset name.

        Returns:
            CompactionStats with before/after version and data file count.

        Raises:
            StorageError: If dataset does not exist or name is invalid.
        """
        self._validate_name(name)
        lance_dir = self._lance_dir(name)

        # Count data files before compaction
        data_dir = lance_dir / "data"
        data_files_before = len(list(data_dir.glob("*.lance"))) if data_dir.exists() else 0

        table = self._open_lance(self._get_dataset_path(name))
        version_before = table.version

        table.optimize()

        version_after = table.version

        # Count data files after compaction
        data_files_after = len(list(data_dir.glob("*.lance"))) if data_dir.exists() else 0

        return CompactionStats(
            version_before=version_before,
            version_after=version_after,
            fragments_before=data_files_before,
            fragments_after=data_files_after,
        )

    def add_column(self, name: str, column_name: str, sql_expr: str) -> None:
        """Add a new column to a dataset via SQL expression.

        Args:
            name: Dataset name.
            column_name: Name of the new column.
            sql_expr: SQL expression for the column (e.g. "CAST(0 AS INT)").

        Raises:
            StorageError: If dataset does not exist or name/column invalid.
        """
        self._validate_name(name)
        self._validate_identifier(column_name, "column_name")
        self._validate_sql_expr(sql_expr)
        table = self._open_lance(self._get_dataset_path(name))
        table.add_columns({column_name: sql_expr})

    def alter_column(self, name: str, column_name: str, new_type: pa.DataType) -> None:
        """Change a column's data type.

        Args:
            name: Dataset name.
            column_name: Column to alter.
            new_type: New pyarrow data type.

        Raises:
            StorageError: If dataset does not exist or name invalid.
        """
        self._validate_name(name)
        self._validate_identifier(column_name, "column_name")
        table = self._open_lance(self._get_dataset_path(name))
        table.alter_columns({"path": column_name, "data_type": new_type})

    def drop_column(self, name: str, column_name: str) -> None:
        """Remove a column from a dataset.

        Args:
            name: Dataset name.
            column_name: Column to drop.

        Raises:
            StorageError: If dataset does not exist or column not found.
        """
        self._validate_name(name)
        self._validate_identifier(column_name, "column_name")
        table = self._open_lance(self._get_dataset_path(name))
        try:
            table.drop_columns([column_name])
        except RuntimeError as exc:
            msg = str(exc)
            if "does not exist" in msg:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=(f"Column '{column_name}' does not exist in dataset '{name}'"),
                ) from exc
            raise

    def get_version(self, name: str) -> int:
        """Get the current version number of a dataset.

        Args:
            name: Dataset name.

        Returns:
            Current version number.

        Raises:
            StorageError: If dataset does not exist or name is invalid.
        """
        self._validate_name(name)
        table = self._open_lance(self._get_dataset_path(name))
        return cast(int, table.version)

    def list_versions(self, name: str) -> list[dict[str, object]]:
        """List all versions of a dataset with metadata.

        Args:
            name: Dataset name.

        Returns:
            List of version metadata dicts, each containing
            'version', 'timestamp', and 'metadata'.
        """
        self._validate_name(name)
        table = self._open_lance(self._get_dataset_path(name))
        raw_versions = table.list_versions()
        return [
            {
                "version": v["version"],
                "timestamp": v["timestamp"],
                "metadata": v["metadata"],
            }
            for v in raw_versions
        ]

    def create_tag(self, name: str, tag: str, version: int | None = None) -> None:
        """Create a named tag for a dataset version.

        Args:
            name: Dataset name.
            tag: Tag name.
            version: Version to tag (defaults to latest).

        Raises:
            StorageError: If dataset does not exist, name/tag invalid, or tag already exists.
        """
        self._validate_name(name)
        self._validate_identifier(tag, "tag")
        table = self._open_lance(self._get_dataset_path(name))
        if version is None:
            version = table.version
        try:
            table.tags.create(tag, version=version)
        except Exception as exc:
            msg = str(exc).lower()
            if "already" in msg or "exists" in msg:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=f"Tag '{tag}' already exists for dataset '{name}'",
                ) from exc
            raise

    def list_tags(self, name: str) -> dict[str, int]:
        """List all tags for a dataset.

        Args:
            name: Dataset name.

        Returns:
            Dict mapping tag names to version numbers.

        Raises:
            StorageError: If a tag cannot be resolved or name is invalid.
        """
        self._validate_name(name)
        import lance

        lance_dir = self._lance_dir(name)
        table = self._open_lance(self._get_dataset_path(name))
        tag_names = list(table.tags.list())
        result: dict[str, int] = {}
        for tag_name in tag_names:
            try:
                ds = lance.dataset(str(lance_dir), version=tag_name)
                result[tag_name] = ds.version
            except Exception as exc:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Failed to resolve tag '{tag_name}' for dataset '{name}'",
                ) from exc
        return result

    def delete_tag(self, name: str, tag: str) -> None:
        """Delete a named tag from a dataset.

        Args:
            name: Dataset name.
            tag: Tag name.

        Raises:
            StorageError: If dataset does not exist, name/tag invalid, or tag not found.
        """
        self._validate_name(name)
        self._validate_identifier(tag, "tag")
        table = self._open_lance(self._get_dataset_path(name))
        try:
            table.tags.delete(tag)
        except Exception as exc:
            msg = str(exc).lower()
            if "not found" in msg or "does not exist" in msg:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Tag '{tag}' not found for dataset '{name}'",
                ) from exc
            raise

    def read_at_tag(self, name: str, tag: str) -> pa.Table:
        """Read dataset data at a specific tag.

        Args:
            name: Dataset name.
            tag: Tag name.

        Returns:
            Arrow Table with the tagged version's data.

        Raises:
            StorageError: If dataset or tag does not exist, or name/tag invalid.
        """
        self._validate_name(name)
        self._validate_identifier(tag, "tag")
        import lance

        lance_dir = self._lance_dir(name)

        if not lance_dir.is_dir():
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' not found",
            )

        try:
            ds = lance.dataset(str(lance_dir), version=tag)
        except Exception as exc:
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Tag '{tag}' not found for dataset '{name}'",
            ) from exc

        return ds.to_table()

    def _write_lance(self, data: pa.Table, path: str, mode: str = "create") -> None:
        """Write data to Lance format via lancedb."""
        import lancedb

        db = lancedb.connect(self.base_uri)

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

        db = lancedb.connect(self.base_uri)
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

    def scan_dataset(
        self,
        name: str,
        *,
        columns: list[str] | None = None,
        filter_expr: str | None = None,
        batch_size: int = 10_000,
    ) -> pa.RecordBatchReader:
        """Stream a Lance dataset as RecordBatchReader (zero materialization).

        Returns a reader that yields batches on-demand instead of materializing
        the entire dataset into memory. Suitable for registering with DuckDB.

        Args:
            name: Dataset name.
            columns: Optional column subset to read.
            filter_expr: Optional Lance filter expression (pushed down).
            batch_size: Rows per batch (default 10k).

        Returns:
            pa.RecordBatchReader streaming the dataset.

        Raises:
            StorageError: If dataset does not exist or name is invalid.
        """
        self._validate_name(name)
        lance_dir = self._lance_dir(name)

        if not lance_dir.is_dir():
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' not found",
            )

        import lance

        ds = lance.dataset(str(lance_dir))
        scanner = ds.scanner(
            columns=columns,
            filter=filter_expr,
            batch_size=batch_size,
        )
        return scanner.to_reader()

    def restore_dataset(self, name: str, data: pa.Table) -> None:
        """Delete and recreate a dataset with new data (used for rollback).

        Args:
            name: Dataset name.
            data: Arrow table to write.

        Raises:
            StorageError: If dataset does not exist or write fails.
        """
        self._validate_name(name)
        self.delete_dataset(name)
        self._write_lance(data, self._get_dataset_path(name), mode="create")
