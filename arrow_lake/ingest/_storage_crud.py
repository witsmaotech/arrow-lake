"""StorageCRUDMixin -- create, read, append, delete, upsert, and row operations."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pyarrow as pa

from arrow_lake.config import StorageBackend
from arrow_lake.exceptions import ErrorCode, StorageError


class StorageCRUDMixin:
    """CRUD operations for Lance datasets."""

    def create_dataset(self, name: str, data: pa.Table) -> None:
        """Create a new Lance dataset.

        Args:
            name: Dataset name.
            data: Arrow table to write.

        Raises:
            StorageError: If dataset already exists or name is invalid.
        """
        lock = self._dataset_lock(name)
        self._acquire_dataset_lock(name)
        try:
            self._validate_name(name)
            if self.dataset_exists(name):
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=f"Dataset '{name}' already exists",
                )
            path = self._get_dataset_path(name)
            self._write_lance(data, path, mode="create")
        finally:
            lock.release()

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

        if not self.dataset_exists(name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' not found",
            )

        if version is not None:
            import lance

            lance_uri = self.dataset_uri(name)
            try:
                ds = lance.dataset(
                    lance_uri, version=version, storage_options=self._storage_options
                )
            except (ValueError, OSError) as exc:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Version {version} not found for dataset '{name}'",
                ) from exc

            if columns is not None:
                return ds.to_table(columns=columns)
            return ds.to_table()

        table = self._open_lance(self._get_dataset_path(name))

        arrow_table = table.to_arrow()
        if columns is not None:
            return arrow_table.select(columns)
        return arrow_table

    def append_dataset(self, name: str, data: pa.Table) -> None:
        """Append data to an existing Lance dataset.

        Args:
            name: Dataset name.
            data: Arrow table to append.

        Raises:
            StorageError: If dataset does not exist or name is invalid.
        """
        lock = self._dataset_lock(name)
        self._acquire_dataset_lock(name)
        try:
            self._validate_name(name)
            path = self._get_dataset_path(name)
            if not self.dataset_exists(name):
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Dataset '{name}' does not exist, cannot append",
                )
            self._write_lance(data, path, mode="append")
        finally:
            lock.release()

    def update_field_comments(self, name: str, comments: dict[str, str]) -> None:
        """In-place update of column ``comment`` field metadata.

        Uses Lance 7's ``update_field_metadata`` (incremental, no data rewrite)
        so the comment is persisted to the manifest cheaply. Used by DB
        comment capture and the ``/schema/annotate`` endpoint. Best-effort for
        callers: a missing dataset raises StorageError; callers that want
        non-fatal behavior wrap in try/except.
        """
        self._validate_name(name)
        if not self.dataset_exists(name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{name}' does not exist, cannot annotate",
            )
        payload = {c: {"comment": v} for c, v in comments.items() if c}
        if not payload:
            return
        import lance

        ds = lance.dataset(
            self.dataset_uri(name), storage_options=self._storage_options
        )
        ds.update_field_metadata(payload, replace=False)

    def delete_dataset(self, name: str) -> None:
        """Delete a Lance dataset.

        Args:
            name: Dataset name.

        Raises:
            StorageError: If dataset does not exist or name is invalid.
        """
        lock = self._dataset_lock(name)
        self._acquire_dataset_lock(name)
        try:
            self._validate_name(name)
            if self._storage_config and self._storage_config.backend != StorageBackend.LOCAL:
                import lancedb

                if not self.dataset_exists(name):
                    raise StorageError(
                        error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                        message=f"Dataset '{name}' does not exist",
                    )
                db = lancedb.connect(self._connect_uri, storage_options=self._storage_options)
                db.drop_table(name)
                return
            import shutil

            path = self._lance_dir(name)
            if not path.is_dir():
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Dataset '{name}' does not exist at {path}",
                )
            shutil.rmtree(path)
        finally:
            lock.release()
        # Clean up lock for deleted dataset to prevent unbounded growth
        with contextlib.suppress(KeyError):
            del self._dataset_locks[name]

    def dataset_exists(self, name: str) -> bool:
        """Check if a dataset exists.

        Args:
            name: Dataset name.

        Returns:
            True if the dataset directory exists.
        """
        self._validate_name(name)
        if self._storage_config and self._storage_config.backend != StorageBackend.LOCAL:
            db = self._get_db()
            result = db.list_tables()
            tables = result.tables if hasattr(result, "tables") else result
            return name in tables
        return self._lance_dir(name).is_dir()

    def list_datasets(self) -> list[str]:
        """List all dataset names.

        Returns:
            Sorted list of dataset names (without .lance suffix).
        """
        if self._storage_config and self._storage_config.backend != StorageBackend.LOCAL:
            db = self._get_db()
            result = db.list_tables()
            tables = result.tables if hasattr(result, "tables") else result
            return sorted(tables)
        base = Path(self.base_uri)
        if not base.exists():
            return []
        return sorted(p.stem for p in base.iterdir() if p.is_dir() and p.name.endswith(".lance"))

    def upsert_dataset(
        self,
        name: str,
        data: pa.Table,
        *,
        on: str = "id",
    ) -> None:
        """Upsert rows using merge_insert on a key column.

        New rows are inserted; existing rows matching ``on`` are updated.
        If the dataset does not exist it is created automatically.

        Args:
            name: Dataset name.
            data: Arrow table with rows to upsert.
            on: Column name to use as the merge key.

        Raises:
            StorageError: If name is invalid or the on-column is not found.
        """
        self._validate_name(name)
        self._validate_identifier(on, "on_column")

        lock = self._dataset_lock(name)
        self._acquire_dataset_lock(name)
        try:
            if not self.dataset_exists(name):
                self.create_dataset(name, data)
                return

            table = self._open_lance(self._get_dataset_path(name))
            if on not in table.schema.names:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Merge key column '{on}' not found in dataset '{name}'",
                )

            try:
                table.merge_insert(on=on).when_matched_update_all().when_not_matched_insert_all().execute(data)
            except (ValueError, RuntimeError, OSError) as exc:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=f"Upsert failed on dataset '{name}': {exc}",
                ) from exc
        finally:
            lock.release()

    def delete_rows(
        self,
        name: str,
        where: str,
    ) -> int:
        """Delete rows matching a filter expression.

        Args:
            name: Dataset name.
            where: SQL WHERE expression (validated for safety).

        Returns:
            Number of rows deleted.

        Raises:
            StorageError: If dataset not found or expression is unsafe.
        """
        lock = self._dataset_lock(name)
        self._acquire_dataset_lock(name)
        try:
            self._validate_name(name)
            self._validate_sql_expr(where)

            table = self._open_lance(self._get_dataset_path(name))
            count_before = table.count_rows()

            try:
                table.delete(where=where)
            except (ValueError, RuntimeError, OSError) as exc:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=f"Row deletion failed on dataset '{name}': {exc}",
                ) from exc

            count_after = table.count_rows()
            return count_before - count_after
        finally:
            lock.release()

    def update_rows(
        self,
        name: str,
        where: str,
        values: dict[str, str],
    ) -> None:
        """Update rows matching a filter expression with new values.

        Args:
            name: Dataset name.
            where: SQL WHERE expression (validated for safety).
            values: Dict mapping column names to SQL expressions for new values.

        Raises:
            StorageError: If dataset not found or expression is unsafe.
        """
        lock = self._dataset_lock(name)
        self._acquire_dataset_lock(name)
        try:
            self._validate_name(name)
            self._validate_sql_expr(where)

            for col in values:
                self._validate_identifier(col, "update_column")

            table = self._open_lance(self._get_dataset_path(name))

            for col in values:
                if col not in table.schema.names:
                    raise StorageError(
                        error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                        message=f"Column '{col}' not found in dataset '{name}'",
                    )

            try:
                table.update(where=where, values=values)
            except (ValueError, RuntimeError, OSError) as exc:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=f"Row update failed on dataset '{name}': {exc}",  # nosec B608
                ) from exc
        finally:
            lock.release()

    def restore_dataset(self, name: str, data: pa.Table) -> None:
        """Delete and recreate a dataset with new data (used for rollback).

        Uses the same cached LanceDB connection for both drop and create
        to avoid stale metadata on S3/MinIO backends.

        Args:
            name: Dataset name.
            data: Arrow table to write.

        Raises:
            StorageError: If dataset does not exist or write fails.
        """
        self._validate_name(name)
        db = self._get_db()
        with contextlib.suppress(Exception):
            db.drop_table(name)
        db.create_table(name, data)
