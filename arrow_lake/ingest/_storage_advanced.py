"""StorageAdvancedMixin -- compaction, schema migration, scan, and dataset admin."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyarrow as pa

from arrow_lake.exceptions import ErrorCode, StorageError

if TYPE_CHECKING:
    from arrow_lake.ingest.storage import CompactionStats


class StorageAdvancedMixin:
    """Advanced storage operations: compaction, schema migration, scan, and admin."""

    def compact(self, name: str) -> CompactionStats:
        """Compact a dataset by merging small fragment files.

        Also cleans up old versions to reclaim disk space.

        Args:
            name: Dataset name.

        Returns:
            CompactionStats with before/after version and data file count.

        Raises:
            StorageError: If dataset does not exist or name is invalid.
        """
        with self._dataset_lock(name):
            self._validate_name(name)

            import lance as lance_lib

            # Count fragments before compaction via lance API
            uri = self.dataset_uri(name) if self._storage_config else str(self._lance_dir(name))
            ds = lance_lib.dataset(uri, storage_options=self._storage_options)
            fragments_before = len(ds.get_fragments())

            table = self._open_lance(self._get_dataset_path(name))
            version_before = table.version

            table.optimize()

            # Cleanup old versions to reclaim disk space
            table.cleanup_old_versions()

            version_after = table.version

            # Count fragments after compaction
            ds = lance_lib.dataset(uri, storage_options=self._storage_options)
            fragments_after = len(ds.get_fragments())

            from arrow_lake.ingest.storage import CompactionStats

            return CompactionStats(
                version_before=version_before,
                version_after=version_after,
                fragments_before=fragments_before,
                fragments_after=fragments_after,
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

    def add_columns_table(self, name: str, columns: pa.Table) -> None:
        """Add pre-computed columns to a dataset without full rewrite.

        Uses Lance's native ``add_columns`` to append columns in-place,
        avoiding the cost of reading + rewriting the entire dataset.

        The ``columns`` table must have the same number of rows as the
        target dataset. Column names must not already exist.

        Args:
            name: Dataset name.
            columns: Arrow Table with new columns (row-aligned).

        Raises:
            StorageError: If dataset not found or column addition fails.
        """
        self._validate_name(name)
        for col_name in columns.column_names:
            self._validate_identifier(col_name, "add_column")
        try:
            import lance as lance_lib
            uri = self.dataset_uri(name)
            ds = lance_lib.dataset(uri, storage_options=self._storage_options)
            ds.add_columns(columns)
        except (OSError, ValueError, RuntimeError) as exc:
            raise StorageError(
                error_code=ErrorCode.STORAGE_WRITE_FAILED,
                message=f"Failed to add columns to '{name}': {exc}",
            ) from exc

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

        if self._storage_options is None and not lance_dir.is_dir():
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' not found",
            )

        import lance

        uri = self.dataset_uri(name) if self._storage_config else str(lance_dir)
        ds = lance.dataset(uri, storage_options=self._storage_options)
        scanner = ds.scanner(
            columns=columns,
            filter=filter_expr,
            batch_size=batch_size,
        )
        return scanner.to_reader()

    def rename_dataset(self, name: str, new_name: str) -> None:
        """Rename a dataset (copy + delete original).

        Args:
            name: Current dataset name.
            new_name: Target dataset name.

        Raises:
            StorageError: If source not found, target already exists, or names invalid.
        """
        self._validate_name(name)
        self._validate_name(new_name)

        if not self.dataset_exists(name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' not found",
            )
        if self.dataset_exists(new_name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_WRITE_FAILED,
                message=f"Dataset '{new_name}' already exists",
            )

        data = self.read_dataset(name)
        self.create_dataset(new_name, data)
        self.delete_dataset(name)

    def copy_dataset(
        self,
        name: str,
        new_name: str,
    ) -> None:
        """Copy a dataset to a new name.

        Args:
            name: Source dataset name.
            new_name: Target dataset name.

        Raises:
            StorageError: If source not found, target already exists, or names invalid.
        """
        self._validate_name(name)
        self._validate_name(new_name)

        if not self.dataset_exists(name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' not found",
            )
        if self.dataset_exists(new_name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_WRITE_FAILED,
                message=f"Dataset '{new_name}' already exists",
            )

        data = self.read_dataset(name)
        self.create_dataset(new_name, data)

    def merge_datasets(
        self,
        source_names: list[str],
        target_name: str,
    ) -> None:
        """Merge multiple datasets into a single target.

        All sources must have identical schemas.

        Args:
            source_names: List of source dataset names.
            target_name: Name for the new merged dataset.

        Raises:
            StorageError: If any source not found, target exists, or schema mismatch.
        """
        self._validate_name(target_name)

        for name in source_names:
            self._validate_name(name)
            if not self.dataset_exists(name):
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Source dataset '{name}' not found",
                )

        if self.dataset_exists(target_name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_WRITE_FAILED,
                message=f"Target dataset '{target_name}' already exists",
            )

        if not source_names:
            raise StorageError(
                error_code=ErrorCode.VALIDATION_MISSING_FIELD,
                message="source_names must not be empty",
            )

        tables = [self.read_dataset(name) for name in source_names]

        reference_schema = tables[0].schema
        for i, t in enumerate(tables[1:], start=1):
            if not t.schema.equals(reference_schema):
                raise StorageError(
                    error_code=ErrorCode.INGEST_SCHEMA_MISMATCH,
                    message=(
                        f"Schema mismatch: source '{source_names[i]}' "
                        f"does not match source '{source_names[0]}'"
                    ),
                )

        merged = pa.concat_tables(tables)
        self.create_dataset(target_name, merged)
