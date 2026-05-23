"""Admin mixin — catalog, dataset management, workflows, versioning, backup, health."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

from arrow_lake.exceptions import StorageError

if TYPE_CHECKING:
    from arrow_lake._models import CatalogResult, HealthInfo
    from arrow_lake.ops.backup import BackupInfo


class _LakeAdminMixin:
    """Provides catalog listing, dataset management, workflow introspection, versioning, backup, and health."""

    def _trace_span(self, name: str, **attrs: Any) -> Any:
        from arrow_lake.api.telemetry import get_tracer
        return get_tracer().start_as_current_span(name, attributes=attrs)

    def catalog(self) -> CatalogResult:
        """List all datasets with metadata (Story 7.1).

        Returns:
            CatalogResult with dataset entries (name, version, row count).
        """
        from arrow_lake.core.metrics import catalog_queries_total, get_metrics_enabled

        if get_metrics_enabled():
            catalog_queries_total.inc()
        from arrow_lake._models import CatalogEntry, CatalogResult

        storage = self._get_storage()
        names = storage.list_datasets()
        entries: list[CatalogEntry] = []
        for name in names:
            try:
                ds = storage.open_dataset(name)
                num_rows = ds.count_rows()
            except (StorageError, OSError):
                num_rows = 0
            try:
                version = storage.get_version(name)
            except (StorageError, OSError):
                version = 0
            entries.append(CatalogEntry(name=name, version=version, num_rows=num_rows))
        return CatalogResult(datasets=entries, total=len(entries))

    def list_datasets(self) -> list[str]:
        """List all dataset names.

        Returns:
            Sorted list of dataset name strings.
        """
        return self._get_storage().list_datasets()

    def open_dataset(self, name: str) -> Any:
        """Open a dataset and return the underlying Lance dataset object.

        Args:
            name: Dataset name.

        Returns:
            The opened Lance dataset.

        Raises:
            StorageError: If the dataset does not exist.
        """
        return self._get_storage().open_dataset(name)

    def delete_dataset(self, name: str) -> None:
        """Delete a dataset and all its data.

        Args:
            name: Dataset name to delete.
        """
        with self._trace_span("delete_dataset", dataset=name):
            self._get_storage().delete_dataset(name)
        from arrow_lake.core.metrics import catalog_tables_total, get_metrics_enabled

        if get_metrics_enabled():
            val = catalog_tables_total._value.get()
            if val is not None and val > 0:
                catalog_tables_total.dec()

    def restore_dataset(self, name: str, data: Any) -> None:
        """Replace a dataset entirely with new data (delete + recreate).

        Used for schema changes, column additions, and full dataset reloads.

        Args:
            name: Dataset name.
            data: Arrow table with the new dataset content.

        Raises:
            StorageError: If dataset does not exist or write fails.
        """
        self._get_storage().restore_dataset(name, data)

    def get_dataset_version(self, name: str) -> int:
        """Get the current version number of a dataset.

        Args:
            name: Dataset name.

        Returns:
            Version number (0-based).
        """
        return self._get_storage().get_version(name)

    def list_dataset_versions(self, name: str) -> list[dict[str, Any]]:
        """List all versions of a dataset.

        Args:
            name: Dataset name.

        Returns:
            List of version metadata dicts.
        """
        return self._get_storage().list_versions(name)

    def add_column(self, name: str, column_name: str, sql_expr: str) -> None:
        """Add a computed column to an existing dataset.

        Args:
            name: Dataset name.
            column_name: Name of the new column.
            sql_expr: SQL expression for the column value (e.g. "price * 0.9").
        """
        self._get_storage().add_column(name, column_name, sql_expr)

    def add_columns_table(self, name: str, columns: Any) -> None:
        """Add pre-computed columns to a dataset without full rewrite.

        Avoids the cost of ``restore_dataset`` (drop + recreate) by using
        Lance's native in-place column addition.

        Args:
            name: Dataset name.
            columns: Arrow Table with new columns (must be row-aligned).
        """
        self._get_storage().add_columns_table(name, columns)

    def alter_column(self, name: str, column_name: str, new_type: Any) -> None:
        """Change the data type of an existing column.

        Args:
            name: Dataset name.
            column_name: Column to modify.
            new_type: Target PyArrow data type (e.g. pa.float32()).
        """
        self._get_storage().alter_column(name, column_name, new_type)

    def drop_column(self, name: str, column_name: str) -> None:
        """Remove a column from a dataset.

        Args:
            name: Dataset name.
            column_name: Column to remove.
        """
        self._get_storage().drop_column(name, column_name)

    def compact_dataset(self, name: str) -> Any:
        """Compact a dataset by merging small fragments.

        Args:
            name: Dataset name.

        Returns:
            CompactionStats with before/after file counts and sizes.
        """
        with self._trace_span("compact_dataset", dataset=name):
            return self._get_storage().compact(name)

    def read_dataset(self, name: str, *, columns: list[str] | None = None) -> Any:
        """Read a dataset as an Arrow table.

        Args:
            name: Dataset name.
            columns: Optional column names to read (None = all).
        """
        return self._get_storage().read_dataset(name, columns=columns)

    def scan_dataset(self, name: str, **kwargs: Any) -> Any:
        """Create a Lance dataset scanner for lazy row-by-row reading.

        Args:
            name: Dataset name.
            **kwargs: Scanner options (columns, filter, etc.).
        """
        return self._get_storage().scan_dataset(name, **kwargs)

    def rename_dataset(self, name: str, new_name: str) -> None:
        """Rename a dataset (copy + delete original).

        Args:
            name: Current dataset name.
            new_name: Target dataset name.

        Raises:
            StorageError: If source not found, target already exists, or names invalid.
        """
        from arrow_lake.core.metrics import _QueryTimer

        with _QueryTimer("rename_dataset"):
            self._get_storage().rename_dataset(name, new_name)

    def copy_dataset(self, name: str, new_name: str) -> None:
        """Copy a dataset to a new name.

        Args:
            name: Source dataset name.
            new_name: Target dataset name.

        Raises:
            StorageError: If source not found, target already exists, or names invalid.
        """
        from arrow_lake.core.metrics import _QueryTimer

        with _QueryTimer("copy_dataset"):
            self._get_storage().copy_dataset(name, new_name)

    def merge_datasets(self, source_names: list[str], target_name: str) -> None:
        """Merge multiple source datasets into a single target.

        All sources must have identical schemas.

        Args:
            source_names: List of source dataset names.
            target_name: Name for the new merged dataset.

        Raises:
            StorageError: If any source not found, target exists, or schema mismatch.
        """
        from arrow_lake.core.metrics import _QueryTimer

        with _QueryTimer("merge_datasets"):
            self._get_storage().merge_datasets(source_names, target_name)

    def backup_create(
        self,
        dataset_names: list[str] | None = None,
        *,
        blob_prefixes: list[str] | None = None,
        backup_id: str | None = None,
    ) -> BackupInfo:
        """Create a backup of Lance datasets and/or blob prefixes.

        Args:
            dataset_names: Datasets to back up (None = none).
            blob_prefixes: Blob prefixes to back up (None = none).
            backup_id: Custom backup ID (None = auto-generate).

        Returns:
            BackupInfo with backup metadata.
        """
        from arrow_lake.ops.backup import BackupManager
        from arrow_lake.storage.blob_store import BlobStoreManager

        sc = self._config.storage
        blob_store = self._get_component(
            "blob_store",
            lambda: BlobStoreManager(config=sc),
        )
        mgr = BackupManager(
            storage_config=sc,
            lance_base_uri=self._base_uri,
            blob_store=blob_store,
        )
        with self._trace_span("backup_create", datasets=len(dataset_names or [])):
            return mgr.create_backup(
                dataset_names=dataset_names,
                blob_prefixes=blob_prefixes,
                backup_id=backup_id,
            )

    def backup_restore(
        self,
        backup_id: str,
        *,
        dataset_names: list[str] | None = None,
        blob_prefixes: list[str] | None = None,
        overwrite: bool = False,
    ) -> BackupInfo:
        """Restore a backup.

        Args:
            backup_id: Backup ID to restore.
            dataset_names: Datasets to restore (None = all from backup).
            blob_prefixes: Blob prefixes to restore (None = all from backup).
            overwrite: Whether to overwrite existing datasets.

        Returns:
            BackupInfo with restored backup metadata.
        """
        from arrow_lake.ops.backup import BackupManager
        from arrow_lake.storage.blob_store import BlobStoreManager

        sc = self._config.storage
        blob_store = self._get_component(
            "blob_store",
            lambda: BlobStoreManager(config=sc),
        )
        mgr = BackupManager(
            storage_config=sc,
            lance_base_uri=self._base_uri,
            blob_store=blob_store,
        )
        with self._trace_span("backup_restore", backup_id=backup_id):
            return mgr.restore_backup(
                backup_id,
                dataset_names=dataset_names,
                blob_prefixes=blob_prefixes,
                overwrite=overwrite,
        )

    def backup_list(self) -> list[BackupInfo]:
        """List all available backups.

        Returns:
            List of BackupInfo sorted by creation time (newest first).
        """
        from arrow_lake.ops.backup import BackupManager
        from arrow_lake.storage.blob_store import BlobStoreManager

        sc = self._config.storage
        blob_store = self._get_component(
            "blob_store",
            lambda: BlobStoreManager(config=sc),
        )
        mgr = BackupManager(
            storage_config=sc,
            lance_base_uri=self._base_uri,
            blob_store=blob_store,
        )
        return mgr.list_backups()

    def backup_delete(self, backup_id: str) -> None:
        """Delete a backup and all its data.

        Args:
            backup_id: Backup ID to delete.
        """
        from arrow_lake.ops.backup import BackupManager
        from arrow_lake.storage.blob_store import BlobStoreManager

        sc = self._config.storage
        blob_store = self._get_component(
            "blob_store",
            lambda: BlobStoreManager(config=sc),
        )
        mgr = BackupManager(
            storage_config=sc,
            lance_base_uri=self._base_uri,
            blob_store=blob_store,
        )
        mgr.delete_backup(backup_id)

    def health(self) -> HealthInfo:
        """Return health status of the Arrow Lake SDK instance.

        Checks storage accessibility, DuckDB session pool stats, and uptime.

        Returns:
            HealthInfo with comprehensive health status.
        """
        from arrow_lake._models import HealthInfo
        from arrow_lake._version import __version__

        sc = self._config.storage
        base_uri = sc.base_uri
        storage_status = "not_found"
        storage_ok = False

        if base_uri.startswith("s3://"):
            try:
                import urllib.request

                endpoint = sc.s3_endpoint
                if endpoint:
                    health_url = endpoint.rstrip("/") + "/minio/health/live"
                    urllib.request.urlopen(health_url, timeout=3)
                    storage_status = "accessible"
                    storage_ok = True
                else:
                    storage_status = "no_endpoint_configured"
            except (ImportError, OSError):
                storage_status = "endpoint_unreachable"
        elif os.path.isdir(base_uri):
            storage_status = "accessible"
            storage_ok = True

        status = "ok" if storage_ok else "degraded"
        uptime = time.monotonic() - self._start_time

        session_pool_info: dict[str, Any] | None = None
        sm = self._components.get("session_manager")
        if sm is not None:
            stats = sm.get_stats()
            session_pool_info = {
                "pool_size": stats.pool_size,
                "active_sessions": stats.active_sessions,
                "queued_requests": stats.queued_requests,
                "total_queries": stats.total_queries,
                "total_errors": stats.total_errors,
                "total_timeouts": stats.total_timeouts,
                "avg_wait_seconds": stats.avg_wait_seconds,
                "slow_query_count": stats.slow_query_count,
            }

        return HealthInfo(
            status=status,
            version=__version__,
            storage_status=storage_status,
            storage_ok=storage_ok,
            uptime_seconds=round(uptime, 2),
            session_pool=session_pool_info,
        )

    def list_flows(self) -> list[str]:
        """List all registered Metaflow workflow names (Epic 6).

        Returns:
            Sorted list of registered flow names.
        """
        import flows

        flows._register_flows()
        return flows.FlowRegistry.list_flows()

    def get_flow_info(self, name: str) -> dict[str, Any]:
        """Get metadata for a registered Metaflow workflow (Epic 6).

        Args:
            name: Registered flow name.

        Returns:
            Dict with flow class name, module, and docstring.

        Raises:
            WorkflowError: If flow is not registered.
        """
        import flows

        from arrow_lake.exceptions import ErrorCode, WorkflowError

        flows._register_flows()
        try:
            flow_cls = flows.FlowRegistry.get(name)
        except KeyError:
            raise WorkflowError(
                error_code=ErrorCode.WORKFLOW_FLOW_NOT_FOUND,
                message=f"Flow '{name}' is not registered",
            ) from None
        return {
            "name": name,
            "class": flow_cls.__name__,
            "module": flow_cls.__module__,
            "doc": flow_cls.__doc__,
        }

    def version(self) -> str:
        """Return the current platform version."""
        from arrow_lake._version import __version__

        return __version__

    # ------------------------------------------------------------------
    # Blob Lifecycle
    # ------------------------------------------------------------------

    def _get_lifecycle_manager(self):
        """Lazy-init BlobLifecycleManager from config."""
        return self._get_component(
            "lifecycle_manager",
            lambda: self._create_lifecycle_manager(),
        )

    def _create_lifecycle_manager(self):
        from arrow_lake.storage.blob_store import BlobStoreManager
        from arrow_lake.storage.lifecycle import BlobLifecycleManager

        sc = self._config.storage
        blob_store = self._get_component(
            "blob_store",
            lambda: BlobStoreManager(config=sc),
        )
        return BlobLifecycleManager(
            config=self._config.lifecycle,
            s3_client=blob_store._s3,
        )

    def lifecycle_apply(self, prefix: str = "") -> dict[str, Any]:
        """Apply lifecycle rules to the S3 bucket/prefix."""
        mgr = self._get_lifecycle_manager()
        return mgr.apply_lifecycle_rules(self._config.storage.s3_bucket, prefix=prefix)

    def lifecycle_status(self, prefix: str = "") -> list[dict[str, str]]:
        """Get storage tier for objects under prefix."""
        from arrow_lake.storage.blob_store import BlobStoreManager

        sc = self._config.storage
        blob_store = self._get_component(
            "blob_store",
            lambda: BlobStoreManager(config=sc),
        )
        blobs = blob_store.list_blobs(prefix=prefix)
        mgr = self._get_lifecycle_manager()
        results = []
        for blob in blobs:
            tier = mgr.get_object_tier(sc.s3_bucket, blob["key"])
            results.append({"key": blob["key"], "tier": tier, "size": str(blob.get("size", 0))})
        return results

    def lifecycle_restore(self, key: str, days: int = 7) -> dict[str, Any]:
        """Restore a Glacier-tiered object for temporary access."""
        mgr = self._get_lifecycle_manager()
        return mgr.restore_object(self._config.storage.s3_bucket, key, days=days)

    def lifecycle_estimate(
        self, total_size_gb: int, target_tier: str = "STANDARD_IA",
    ) -> dict[str, Any]:
        """Estimate monthly cost savings from tier transition."""
        mgr = self._get_lifecycle_manager()
        return mgr.estimate_cost_savings(total_size_gb, "STANDARD", target_tier)

    def lifecycle_rules(self, prefix: str = "") -> dict[str, Any]:
        """Preview lifecycle rules without applying."""
        mgr = self._get_lifecycle_manager()
        rules = mgr._build_lifecycle_rules(prefix)
        return {
            "enabled": self._config.lifecycle.enabled,
            "prefix": prefix or "(root)",
            "standard_to_ia_days": self._config.lifecycle.standard_to_ia_days,
            "ia_to_glacier_days": self._config.lifecycle.ia_to_glacier_days,
            "glacier_expiration_days": self._config.lifecycle.glacier_expiration_days,
            "excluded_prefixes": self._config.lifecycle.excluded_prefixes,
            "rules": rules,
        }
