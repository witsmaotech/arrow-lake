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
        lock = self._dataset_lock(name)
        self._acquire_dataset_lock(name)
        try:
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
        finally:
            lock.release()

    def compact_files(
        self,
        name: str,
        *,
        target_rows_per_fragment: int = 1_024_000,
        max_rows_per_group: int | None = None,
        materialize_deletions: bool = True,
        num_threads: int | None = None,
    ) -> CompactionStats:
        """Fine-grained compaction via lance dataset optimize.compact_files (v1.7.1 #10).

        Unlike :meth:`compact` (which uses ``table.optimize()``), this exposes
        pylance's ``ds.optimize.compact_files`` for explicit control over fragment
        sizing — useful for write-heavy datasets where the default merge heuristic
        is suboptimal.

        Args:
            name: Dataset name.
            target_rows_per_fragment: Target row count per output fragment.
            max_rows_per_group: Max rows per compaction group (None = lance default).
            materialize_deletions: Whether to materialize soft-deletions.
            num_threads: Thread count (None = lance default).

        Raises:
            StorageError: If dataset not found or compaction fails.
        """
        lock = self._dataset_lock(name)
        self._acquire_dataset_lock(name)
        try:
            self._validate_name(name)
            import lance as lance_lib

            uri = self.dataset_uri(name) if self._storage_config else str(self._lance_dir(name))
            ds = lance_lib.dataset(uri, storage_options=self._storage_options)
            fragments_before = len(ds.get_fragments())
            version_before = ds.version

            try:
                ds.optimize.compact_files(
                    target_rows_per_fragment=target_rows_per_fragment,
                    max_rows_per_group=max_rows_per_group,
                    materialize_deletions=materialize_deletions,
                    num_threads=num_threads,
                )
            except (ValueError, RuntimeError, OSError) as exc:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=f"compact_files failed on '{name}': {exc}",
                ) from exc

            ds = lance_lib.dataset(uri, storage_options=self._storage_options)
            from arrow_lake.ingest.storage import CompactionStats

            return CompactionStats(
                version_before=version_before,
                version_after=ds.version,
                fragments_before=fragments_before,
                fragments_after=len(ds.get_fragments()),
            )
        finally:
            lock.release()

    def add_column(self, name: str, column_name: str, sql_expr: str) -> None:
        """Add a new column to a dataset via SQL expression.

        Args:
            name: Dataset name.
            column_name: Name of the new column.
            sql_expr: SQL expression for the column (e.g. "CAST(0 AS INT)").

        Raises:
            StorageError: If dataset does not exist or name/column invalid.
            SchemaMigrationError: If the column already exists.
        """
        from arrow_lake.ingest.schema import SchemaCompatibilityChecker, SchemaMigrationError

        self._validate_name(name)
        self._validate_identifier(column_name, "column_name")
        self._validate_sql_expr(sql_expr)

        table = self._open_lance(self._get_dataset_path(name))
        checker = SchemaCompatibilityChecker(table.schema)
        issues = checker.check_add_column(column_name, pa.string())
        if issues:
            raise SchemaMigrationError("; ".join(issues))

        table.add_columns({column_name: sql_expr})
        self._record_schema_change(name, "add_column", {"column": column_name, "sql_expr": sql_expr})

    def _record_schema_change(self, name: str, change_type: str, details: dict) -> None:
        """v1.9.0: record a dataset schema change to the governance store (fail-soft)."""
        store = getattr(self, "_governance_store", None)
        if store is None:
            return
        try:
            store.record_schema_change(name, change_type, details=details)
        except Exception:  # noqa: BLE001 — governance is best-effort
            pass

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
        self._record_schema_change(name, "add_columns", {"columns": list(columns.column_names)})

    def has_column(self, name: str, column_name: str) -> bool:
        """Return True iff ``column_name`` exists in dataset ``name`` (v1.10.2 P1).

        Used by ``embed_and_add`` to decide first-time-add vs null-backfill.
        Fail-soft: a missing dataset returns False (caller treats as first-time).
        """
        self._validate_name(name)
        try:
            table = self._open_lance(self._get_dataset_path(name))
            return column_name in table.schema.names
        except Exception:  # noqa: BLE001 — dataset missing / unreadable
            return False

    def alter_column(self, name: str, column_name: str, new_type: pa.DataType) -> None:
        """Change a column's data type.

        Args:
            name: Dataset name.
            column_name: Column to alter.
            new_type: New pyarrow data type.

        Raises:
            StorageError: If dataset does not exist or name invalid.
            SchemaMigrationError: If type change is incompatible.
        """
        from arrow_lake.ingest.schema import SchemaCompatibilityChecker, SchemaMigrationError

        self._validate_name(name)
        self._validate_identifier(column_name, "column_name")

        table = self._open_lance(self._get_dataset_path(name))
        checker = SchemaCompatibilityChecker(table.schema)
        issues = checker.check_alter_column(column_name, new_type)
        if issues:
            raise SchemaMigrationError("; ".join(issues))

        table.alter_columns({"path": column_name, "data_type": new_type})
        self._record_schema_change(name, "type_change", {"column": column_name, "new_type": str(new_type)})

    def drop_column(self, name: str, column_name: str) -> None:
        """Remove a column from a dataset.

        Args:
            name: Dataset name.
            column_name: Column to drop.

        Raises:
            StorageError: If dataset does not exist or column not found.
            SchemaMigrationError: If column has an active vector index.
        """
        from arrow_lake.ingest.schema import SchemaCompatibilityChecker, SchemaMigrationError

        self._validate_name(name)
        self._validate_identifier(column_name, "column_name")

        table = self._open_lance(self._get_dataset_path(name))
        checker = SchemaCompatibilityChecker(table.schema)

        # Check indexed columns from config if available
        indexed = frozenset()
        if hasattr(self, "_olap_config") and self._olap_config is not None:
            indexed = frozenset(getattr(self._olap_config, "ducklake_index_columns", []))

        issues = checker.check_drop_column(column_name, indexed_columns=indexed)
        if issues:
            raise SchemaMigrationError("; ".join(issues))

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
        self._record_schema_change(name, "drop_column", {"column": column_name})

    def scan_dataset(
        self,
        name: str,
        *,
        columns: list[str] | None = None,
        filter_expr: str | None = None,
        batch_size: int = 10_000,
        table: str | None = None,
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
        if table is not None:
            self._validate_table_name(table)
        lance_dir = self._lance_dir(name, table)

        if self._storage_options is None and not lance_dir.is_dir():
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' not found",
            )

        import lance

        uri = (
            self.dataset_uri(name, table)
            if self._storage_config else str(lance_dir)
        )
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
