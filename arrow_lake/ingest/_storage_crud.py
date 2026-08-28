"""StorageCRUDMixin -- create, read, append, delete, upsert, and row operations."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pyarrow as pa
import structlog

from arrow_lake.config import StorageBackend
from arrow_lake.exceptions import ErrorCode, StorageError

logger = structlog.get_logger(__name__)


class StorageCRUDMixin:
    """CRUD operations for Lance datasets."""

    def create_dataset(self, name: str, data: pa.Table, *, table: str | None = None) -> None:
        """Create a new Lance dataset (or a table inside a container dataset).

        Args:
            name: Dataset name.
            data: Arrow table to write.
            table: Optional table name — with it, the write targets
                ``{base}/{name}/{table}.lance`` (container dataset, DR14 W1.1).

        Raises:
            StorageError: If the target already exists, the name is invalid,
                or the name already exists in the other shape (single-table
                dataset vs container — identity conflict, D3).
        """
        lock_key = f"{name}/{table}" if table is not None else name
        lock = self._dataset_lock(lock_key)
        self._acquire_dataset_lock(lock_key)
        try:
            self._validate_name(name)
            if table is not None:
                self._validate_table_name(table)
                if self.dataset_exists(name):
                    raise StorageError(
                        error_code=ErrorCode.STORAGE_WRITE_FAILED,
                        message=f"Dataset '{name}' already exists as a single-table dataset",
                    )
                if self.dataset_exists(name, table=table):
                    raise StorageError(
                        error_code=ErrorCode.STORAGE_WRITE_FAILED,
                        message=f"Table '{name}/{table}' already exists",
                    )
            else:
                if self.dataset_exists(name):
                    raise StorageError(
                        error_code=ErrorCode.STORAGE_WRITE_FAILED,
                        message=f"Dataset '{name}' already exists",
                    )
                if self.list_container_tables(name):
                    raise StorageError(
                        error_code=ErrorCode.STORAGE_WRITE_FAILED,
                        message=f"Dataset '{name}' already exists as a container",
                    )
            path = self._get_dataset_path(name, table)
            self._write_lance(data, path, mode="create", container=name if table else None)
        finally:
            lock.release()

    def read_dataset(
        self, name: str, version: int | None = None, columns: list[str] | None = None,
        *, table: str | None = None,
    ) -> pa.Table:
        """Read a Lance dataset (or a table inside a container dataset).

        Args:
            name: Dataset name.
            version: Specific version to read (None = latest).
            columns: Optional column subset to read.
            table: Optional table name within a container dataset.

        Returns:
            Arrow Table with the dataset contents.

        Raises:
            StorageError: If dataset does not exist, name is invalid, or version is invalid.
        """
        self._validate_name(name)
        if table is not None:
            self._validate_table_name(table)

        if not self.dataset_exists(name, table=table):
            target = f"{name}/{table}" if table is not None else name
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{target}' not found",
            )

        if version is not None:
            import lance

            lance_uri = self.dataset_uri(name, table)
            target = f"{name}/{table}" if table is not None else name
            try:
                ds = lance.dataset(
                    lance_uri, version=version, storage_options=self._storage_options
                )
            except (ValueError, OSError) as exc:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Version {version} not found for dataset '{target}'",
                ) from exc

            if columns is not None:
                return ds.to_table(columns=columns)
            return ds.to_table()

        lance_table = self._open_lance(
            self._get_dataset_path(name, table),
            container=name if table else None, table=table,
        )

        arrow_table = lance_table.to_arrow()
        if columns is not None:
            return arrow_table.select(columns)
        return arrow_table

    def append_dataset(self, name: str, data: pa.Table, *, table: str | None = None) -> None:
        """Append data to an existing Lance dataset (or container table).

        Args:
            name: Dataset name.
            data: Arrow table to append.
            table: Optional table name within a container dataset.

        Raises:
            StorageError: If dataset does not exist or name is invalid.
        """
        lock_key = f"{name}/{table}" if table is not None else name
        lock = self._dataset_lock(lock_key)
        self._acquire_dataset_lock(lock_key)
        try:
            self._validate_name(name)
            if table is not None:
                self._validate_table_name(table)
            path = self._get_dataset_path(name, table)
            if not self.dataset_exists(name, table=table):
                target = f"{name}/{table}" if table is not None else name
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Dataset '{target}' does not exist, cannot append",
                )
            data = self._evolve_and_align_schema(name, data, table=table)
            self._write_lance(data, path, mode="append", container=name if table else None)
        finally:
            lock.release()

    def _evolve_and_align_schema(
        self, name: str, data: pa.Table, *, table: str | None = None,
    ) -> pa.Table:
        """Append-time schema alignment (v1.9.6).

        Columns present in ``data`` but missing from the existing dataset
        (e.g. ``page_number``/``doc_type`` added by newer ingest code) are
        added to the dataset via Lance ``add_columns`` (historical rows filled
        NULL). Columns the dataset has but ``data`` lacks are back-filled NULL
        onto ``data``. The result is reordered to the dataset's schema so
        ``mode='append'`` no longer raises ``ValueError: field ... does not
        exist in table schema`` when appending newer-shaped chunks to an older
        dataset. New datasets are unaffected (create path). Best-effort: on
        any failure returns ``data`` unchanged so behavior is no worse than
        before.
        """
        try:
            import lance

            uri = self.dataset_uri(name, table)
            ds = lance.dataset(uri, storage_options=self._storage_options)
            old_names = set(ds.schema.names)
            extra = [c for c in data.column_names if c not in old_names]
            if extra:
                nrows = ds.count_rows()
                null_cols = pa.table(
                    {c: pa.nulls(nrows, type=data.schema.field(c).type) for c in extra}
                )
                ds.add_columns(null_cols)
            fresh = lance.dataset(uri, storage_options=self._storage_options)
            fresh_names = fresh.schema.names
            data_cols = set(data.column_names)
            arrays = [
                data.column(c) if c in data_cols
                else pa.nulls(data.num_rows, type=fresh.schema.field(c).type)
                for c in fresh_names
            ]
            if extra:
                import structlog

                structlog.get_logger(__name__).info(
                    "ingest_append_schema_evolved", dataset=name, added_columns=extra
                )
            return pa.Table.from_arrays(arrays, names=fresh_names)
        except Exception:
            import structlog

            structlog.get_logger(__name__).debug(
                "ingest_append_schema_align_skipped", dataset=name, exc_info=True
            )
            return data

    def update_field_comments(
        self, name: str, comments: dict[str, str], *, table: str | None = None,
    ) -> None:
        """In-place update of column ``comment`` field metadata.

        Uses Lance 7's ``update_field_metadata`` (incremental, no data rewrite)
        so the comment is persisted to the manifest cheaply. Used by DB
        comment capture and the ``/schema/annotate`` endpoint. Best-effort for
        callers: a missing dataset raises StorageError; callers that want
        non-fatal behavior wrap in try/except.
        """
        self._validate_name(name)
        if table is not None:
            self._validate_table_name(table)
        if not self.dataset_exists(name, table=table):
            target = f"{name}/{table}" if table is not None else name
            raise StorageError(
                error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                message=f"Dataset '{target}' does not exist, cannot annotate",
            )
        payload = {c: {"comment": v} for c, v in comments.items() if c}
        if not payload:
            return
        import lance

        ds = lance.dataset(
            self.dataset_uri(name, table), storage_options=self._storage_options
        )
        ds.update_field_metadata(payload, replace=False)

    def delete_dataset(self, name: str, *, table: str | None = None) -> None:
        """Delete a Lance dataset, a container table, or a whole container.

        - ``table`` given: drop only that table inside the container.
        - no ``table`` and ``name`` is a single-table dataset: legacy delete.
        - no ``table`` and ``name`` is a container (has tables): drop ALL
          container tables and remove the container directory.

        Args:
            name: Dataset name.
            table: Optional table name within a container dataset.

        Raises:
            StorageError: If target does not exist or name is invalid.
        """
        lock_key = f"{name}/{table}" if table is not None else name
        lock = self._dataset_lock(lock_key)
        self._acquire_dataset_lock(lock_key)
        table_lock_keys: list[str] = []
        try:
            self._validate_name(name)
            if table is not None:
                self._validate_table_name(table)
                if not self.dataset_exists(name, table=table):
                    raise StorageError(
                        error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                        message=f"Table '{name}/{table}' does not exist",
                    )
                if self._storage_config and self._storage_config.backend != StorageBackend.LOCAL:
                    self._get_db(name).drop_table(table)
                else:
                    import shutil

                    shutil.rmtree(self._lance_dir(name, table))
                return

            container_tables = self.list_container_tables(name)
            is_container = bool(container_tables)
            if is_container:
                # P2-3 (review 2026-08-26 §三): container-level delete and a
                # table-level write lock on different keys could interleave —
                # rmtree vs append left a half-deleted state. Acquire every
                # enumerated table's write lock (sorted → stable order,
                # deadlock-safe) before dropping. Residual race: a table
                # CREATED after this enumeration is not covered.
                for t in sorted(container_tables):
                    key = f"{name}/{t}"
                    self._acquire_dataset_lock(key)
                    table_lock_keys.append(key)
            if self._storage_config and self._storage_config.backend != StorageBackend.LOCAL:
                if is_container:
                    cdb = self._get_db(name)
                    for t in container_tables:
                        cdb.drop_table(t)
                    return
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

            if is_container:
                shutil.rmtree(Path(self.base_uri) / name)
                return
            path = self._lance_dir(name)
            if not path.is_dir():
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Dataset '{name}' does not exist at {path}",
                )
            shutil.rmtree(path)
        finally:
            for key in reversed(table_lock_keys):
                self._dataset_lock(key).release()
            lock.release()
            for key in table_lock_keys:
                with contextlib.suppress(KeyError):
                    self._dataset_locks.pop(key, None)
        # Clean up lock for deleted dataset to prevent unbounded growth
        with contextlib.suppress(KeyError):
            self._dataset_locks.pop(lock_key, None)

    def dataset_exists(self, name: str, *, table: str | None = None) -> bool:
        """Check if a dataset (or a table inside a container dataset) exists.

        Args:
            name: Dataset name.
            table: Optional table name within a container dataset.

        Returns:
            True if the dataset directory exists. A container dataset with
            tables does NOT exist as a single-table dataset (and vice versa).
        """
        self._validate_name(name)
        if table is not None:
            self._validate_table_name(table)
            if self._storage_config and self._storage_config.backend != StorageBackend.LOCAL:
                cdb = self._get_db(name)
                result = cdb.list_tables()
                tables = result.tables if hasattr(result, "tables") else result
                return table in tables
            return self._lance_dir(name, table).is_dir()
        if self._storage_config and self._storage_config.backend != StorageBackend.LOCAL:
            db = self._get_db()
            result = db.list_tables()
            tables = result.tables if hasattr(result, "tables") else result
            return name in tables
        return self._lance_dir(name).is_dir()

    def list_container_tables(self, name: str) -> list[str]:
        """List table names inside a container dataset.

        Enumerates via the container-scoped lancedb connection (authoritative
        for table enumeration; container *identity* remains a control-plane
        registration concern, D3). Returns [] when the dataset is not a
        container / does not exist.
        """
        self._validate_name(name)
        # P2-2 (review 2026-08-26 §三): local branch must not CONNECT on a
        # missing directory — lancedb.connect mkdirs, so a probe (identity
        # guards call this on every write path) would leave a container-
        # shaped garbage directory next to the real data.
        if not (self._storage_config and self._storage_config.backend != StorageBackend.LOCAL):
            if not (Path(self.base_uri) / name).is_dir():
                return []
        try:
            result = self._get_db(name).list_tables()
        except Exception:  # noqa: BLE001 — remote fail-open ≠ "not a container"
            logger.debug("list_container_tables_failed", dataset=name, exc_info=True)
            return []
        tables = result.tables if hasattr(result, "tables") else result
        return sorted(t for t in tables if isinstance(t, str))

    def list_containers_with_tables(self) -> dict[str, list[str]]:
        """Enumerate containers with their tables in ONE pass (P1-6, review
        2026-08-26): ``{name: [tables]}``.

        Callers used to run ``list_containers()`` (which already enumerates
        each candidate's tables to validate it) and then re-query
        ``list_container_tables(name)`` per container — doubling the remote
        LISTs on the catalog path and again on every Gravitino sync cycle.
        The dict is the shared enumeration result; empty-table candidates
        are not containers and are dropped.
        """
        if self._storage_config and self._storage_config.backend != StorageBackend.LOCAL:
            import boto3

            so = self._storage_options or {}
            client = boto3.client(
                "s3",
                endpoint_url=self._storage_config.s3_endpoint,
                aws_access_key_id=self._storage_config.s3_access_key,
                aws_secret_access_key=self._storage_config.s3_secret_key,
                region_name=str(so.get("region") or "us-east-1"),
            )
            # Root prefix the lancedb connection addresses (may be empty).
            after_scheme = self._connect_uri.split("://", 1)[-1]
            root = after_scheme.partition("/")[2].strip("/")
            prefix = f"{root}/" if root else ""
            names: list[str] = []
            token: str | None = None
            while True:
                kwargs: dict[str, Any] = {
                    "Bucket": self._storage_config.s3_bucket, "Delimiter": "/",
                    **({"Prefix": prefix} if prefix else {}),
                }
                if token:
                    kwargs["ContinuationToken"] = token
                resp = client.list_objects_v2(**kwargs)
                for p in resp.get("CommonPrefixes", []):
                    name = str(p.get("Prefix", ""))[len(prefix):].rstrip("/")
                    if name and not name.endswith(".lance") and "/" not in name:
                        names.append(name)
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
            # Same semantics as the local branch: a real container holds at
            # least one table. Filters foreign directories that merely live
            # under the root prefix (e.g. backup manifests). The per-table
            # listings are RETAINED instead of discarded.
            out = {
                n: tables
                for n in names
                if (tables := self.list_container_tables(n))
            }
            return dict(sorted(out.items()))
        base = Path(self.base_uri)
        if not base.exists():
            return {}
        out: dict[str, list[str]] = {}
        for p in sorted(base.iterdir()):
            if not p.is_dir() or p.name.endswith(".lance"):
                continue
            # Authoritative table enumeration (lancedb view), same as the
            # S3 branch — a dir that is not a lancedb container yields [].
            tables = self.list_container_tables(p.name)
            if tables:
                out[p.name] = tables
        return out

    def list_containers(self) -> list[str]:
        """List container dataset names (DR14 W4.2 catalog visibility).

        Single-table datasets live at ``{root}/{name}.lance``; a container is
        a plain ``{root}/{name}/`` directory holding ``*.lance`` tables.

        local: directories under base that are not ``.lance`` tables
        themselves and hold at least one ``*.lance`` child (an empty or
        foreign directory is not a container).
        S3/MinIO: delimiter listing under the lancedb root prefix extracted
        from ``_connect_uri`` (``s3://{bucket}/{prefix}``) — a single-table
        dataset shows up as the ``{name}.lance/`` prefix, anything else at
        that level is a container directory. The root prefix matters: host
        and container configs may address different prefixes in one bucket.

        Prefer :meth:`list_containers_with_tables` — the per-container table
        enumeration this method triggers is otherwise discarded (P1-6).
        """
        return list(self.list_containers_with_tables())

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
        table: str | None = None,
    ) -> None:
        """Upsert rows using merge_insert on a key column.

        New rows are inserted; existing rows matching ``on`` are updated.
        If the dataset does not exist it is created automatically.

        Args:
            name: Dataset name.
            data: Arrow table with rows to upsert.
            on: Column name to use as the merge key.
            table: Optional table name within a container dataset.

        Raises:
            StorageError: If name is invalid or the on-column is not found.
        """
        self._validate_name(name)
        self._validate_identifier(on, "on_column")

        lock_key = f"{name}/{table}" if table is not None else name
        lock = self._dataset_lock(lock_key)
        self._acquire_dataset_lock(lock_key)
        try:
            if not self.dataset_exists(name, table=table):
                if table is not None:
                    self.create_dataset(name, data, table=table)
                else:
                    self.create_dataset(name, data)
                return

            lance_table = self._open_lance(
                self._get_dataset_path(name, table),
                container=name if table else None, table=table,
            )
            if on not in lance_table.schema.names:
                target = f"{name}/{table}" if table is not None else name
                raise StorageError(
                    error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                    message=f"Merge key column '{on}' not found in dataset '{target}'",
                )

            try:
                lance_table.merge_insert(on=on).when_matched_update_all().when_not_matched_insert_all().execute(data)
            except (ValueError, RuntimeError, OSError) as exc:
                target = f"{name}/{table}" if table is not None else name
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=f"Upsert failed on dataset '{target}': {exc}",
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
        *,
        table: str | None = None,
    ) -> None:
        """Update rows matching a filter expression with new values.

        Args:
            name: Dataset name.
            where: SQL WHERE expression (validated for safety).
            values: Dict mapping column names to SQL expressions for new values.
            table: Optional table within a container dataset
                (v1.11.2 W4, D1-①: container addressing mirrors append_dataset;
                used by the action middleware's update_lifecycle effect).

        Raises:
            StorageError: If dataset not found or expression is unsafe.
        """
        lock_key = f"{name}/{table}" if table is not None else name
        lock = self._dataset_lock(lock_key)
        self._acquire_dataset_lock(lock_key)
        try:
            self._validate_name(name)
            if table is not None:
                self._validate_table_name(table)
            self._validate_sql_expr(where)

            for col in values:
                self._validate_identifier(col, "update_column")

            lance_table = self._open_lance(
                self._get_dataset_path(name, table),
                container=name if table else None, table=table,
            )

            for col in values:
                if col not in lance_table.schema.names:
                    target = f"{name}/{table}" if table is not None else name
                    raise StorageError(
                        error_code=ErrorCode.STORAGE_PATH_NOT_FOUND,
                        message=f"Column '{col}' not found in dataset '{target}'",
                    )

            try:
                lance_table.update(where=where, values=values)
            except (ValueError, RuntimeError, OSError) as exc:
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=f"Row update failed on dataset '{name}': {exc}",  # nosec B608
                ) from exc
        finally:
            lock.release()

    def restore_dataset(self, name: str, data: pa.Table, *, table: str | None = None) -> None:
        """Delete and recreate a dataset with new data (used for rollback).

        Uses the same cached LanceDB connection for both drop and create
        to avoid stale metadata on S3/MinIO backends.

        Args:
            name: Dataset name.
            data: Arrow table to write.
            table: Optional table name within a container dataset.

        Raises:
            StorageError: If dataset does not exist, write fails, or the
                restore would create a dual identity (P1-2, review 2026-08-26:
                dropping/recreating one shape while the other exists leaves
                ``{name}.lance`` next to ``{name}/{table}.lance``).
        """
        self._validate_name(name)
        if table is not None:
            self._validate_table_name(table)
            if self.dataset_exists(name):
                raise StorageError(
                    error_code=ErrorCode.STORAGE_WRITE_FAILED,
                    message=(
                        f"Identity conflict for '{name}': restore targets table "
                        f"'{table}' but a single-table dataset already exists"
                    ),
                )
            db = self._get_db(name)
            with contextlib.suppress(Exception):
                db.drop_table(table)
            db.create_table(table, data)
            return
        if self.list_container_tables(name):
            raise StorageError(
                error_code=ErrorCode.STORAGE_WRITE_FAILED,
                message=(
                    f"Identity conflict for '{name}': restore targets the "
                    "single-table dataset but a container already exists"
                ),
            )
        db = self._get_db()
        with contextlib.suppress(Exception):
            db.drop_table(name)
        db.create_table(name, data)
