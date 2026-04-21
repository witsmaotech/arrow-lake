"""Lineage mixin — data lineage recording, history, and querying."""

from __future__ import annotations

from typing import Any


class _LakeLineageMixin:
    """Provides data lineage event recording, history, and SQL querying."""

    def lineage_record_event(
        self,
        dataset_name: str,
        operation: str,
        *,
        source_datasets: list[str] | None = None,
        transform_type: str = "",
        actor: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a lineage event (Story 8.3).

        Args:
            dataset_name: Target dataset name.
            operation: Operation type (create/append/transform/delete).
            source_datasets: Upstream dataset names.
            transform_type: Transformation type.
            actor: Who triggered the event.
            metadata: Additional context.
        """
        from arrow_lake.catalog.lineage import LineageStore, create_lineage_event

        store = self._get_component(
            "lineage",
            lambda: LineageStore(self._get_storage()),
        )
        event = create_lineage_event(
            dataset_name,
            operation,
            source_datasets=source_datasets,
            transform_type=transform_type,
            actor=actor,
            metadata=metadata,
        )
        store.record_event(event)

    def lineage_history(self, dataset_name: str) -> list[Any]:
        """Get lineage history for a dataset (Story 8.3).

        Args:
            dataset_name: Dataset name.

        Returns:
            List of LineageEvent in chronological order.
        """
        from arrow_lake.catalog.lineage import LineageStore

        store = self._get_component(
            "lineage",
            lambda: LineageStore(self._get_storage()),
        )
        return store.get_dataset_history(dataset_name)

    def lineage_query(self, sql: str) -> Any:
        """SQL query over lineage events (Story 8.3).

        Args:
            sql: SELECT-only SQL query.

        Returns:
            Arrow Table with query results.
        """
        from arrow_lake.catalog.lineage import LineageQueryBridge, LineageStore

        store = self._get_component(
            "lineage",
            lambda: LineageStore(self._get_storage()),
        )
        bridge = LineageQueryBridge(store)
        return bridge.query(sql)
