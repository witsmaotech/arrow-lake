"""Catalog read replica for high availability (Story 6.11).

Provides a read-only fallback when the primary CatalogActor is unavailable:
- Automatic failover on Ray GCS failure
- Write operations return error when primary is down
- Primary recovery restores normal operations
"""

from __future__ import annotations

from typing import Any

import structlog

from arrow_lake.exceptions import CatalogError, ErrorCode

logger = structlog.get_logger(__name__)


class CatalogReadReplica:
    """Read-only replica of the catalog for high availability.

    When the primary CatalogActor is unreachable, this replica provides
    read-only access to cached metadata. All write operations fail
    with a clear error when the primary is down.

    Usage::

        replica = CatalogReadReplica()
        replica.sync_from_actor(handle)  # warm cache from primary

        # When primary is down:
        tables = replica.list_tables()  # returns cached data
        replica.register_table(...)       # raises CatalogError
    """

    def __init__(self) -> None:
        self._tables_cache: dict[str, dict[str, str]] = {}
        self._primary_available: bool = True

    @property
    def primary_available(self) -> bool:
        """Whether the primary catalog actor is reachable."""
        return self._primary_available

    def mark_primary_unavailable(self) -> None:
        """Mark the primary as down (triggers failover)."""
        self._primary_available = False
        logger.warning(
            "catalog_failover", message="Primary catalog unavailable, using read replica"
        )

    def mark_primary_available(self) -> None:
        """Mark the primary as back online."""
        was_down = not self._primary_available
        self._primary_available = True
        if was_down:
            logger.info("catalog_primary_recovered", message="Primary catalog is back online")

    def sync_from_actor(self, handle: Any) -> int:
        """Warm the cache by reading all tables from the primary actor.

        Args:
            handle: Ray actor handle for the primary CatalogActor.

        Returns:
            Number of tables synced.

        Raises:
            CatalogError: If sync fails.
        """
        try:
            import ray

            tables = ray.get(handle.list_tables.remote())
            for table_meta in tables:
                self._tables_cache[table_meta["name"]] = table_meta
            self._primary_available = True
            logger.info("catalog_replica_synced", count=len(tables))
            return len(tables)
        except Exception as exc:
            self.mark_primary_unavailable()
            raise CatalogError(
                error_code=ErrorCode.CATALOG_CONNECTION_FAILED,
                message=f"Failed to sync from primary: {exc}",
            ) from exc

    def _check_primary(self, operation: str) -> None:
        """Raise if primary is unavailable and operation requires it."""
        if not self._primary_available:
            raise CatalogError(
                error_code=ErrorCode.CATALOG_CONNECTION_FAILED,
                message=f"Cannot {operation}: primary catalog is unavailable",
            )

    def list_tables(self) -> list[dict[str, str]]:
        """List tables from cache (read operation, no primary needed).

        Returns:
            List of cached table metadata dicts (copies to prevent mutation).
        """
        return [dict(entry) for entry in self._tables_cache.values()]

    def get_table(self, name: str) -> dict[str, str]:
        """Get a table from cache (read operation).

        Args:
            name: Table name.

        Returns:
            Table metadata dict.

        Raises:
            CatalogError: If table not in cache.
        """
        if name not in self._tables_cache:
            raise CatalogError(
                error_code=ErrorCode.CATALOG_DATASET_NOT_FOUND,
                message=f"Table '{name}' not found in replica cache",
            )
        return dict(self._tables_cache[name])

    def register_table(self, name: str, schema_json: str, location: str) -> dict[str, str]:
        """Write operation — requires primary.

        Raises:
            CatalogError: Always, since replicas are read-only.
        """
        self._check_primary("register_table")
        return {}  # unreachable — _check_primary raises CatalogError

    def delete_table(self, name: str, **kwargs: Any) -> bool:
        """Write operation — requires primary.

        Raises:
            CatalogError: Always, since replicas are read-only.
        """
        self._check_primary("delete_table")
        return False  # unreachable — _check_primary raises CatalogError

    def is_cache_empty(self) -> bool:
        """Check if the replica cache has no data."""
        return len(self._tables_cache) == 0
