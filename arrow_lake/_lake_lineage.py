"""Lineage mixin — data lineage recording, history, and querying."""

from __future__ import annotations

from typing import Any

import pyarrow as pa


class _LakeLineageMixin:
    """Provides data lineage event recording, history, and SQL querying."""

    def _create_lineage_store(self):
        """Create a LineageStore and inject auth provider if available."""
        from arrow_lake.catalog.lineage import LineageStore

        store = LineageStore(self._get_storage())
        auth = getattr(self, "_gravitino_auth_provider", None)
        if auth is not None:
            store.set_auth_provider(auth)
        return store

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
        """Record a lineage event (Story 8.3)."""
        from arrow_lake.catalog.lineage import create_lineage_event

        store = self._get_component(
            "lineage",
            lambda: self._create_lineage_store(),
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
        """Get lineage history for a dataset (Story 8.3)."""

        store = self._get_component(
            "lineage",
            lambda: self._create_lineage_store(),
        )
        return store.get_dataset_history(dataset_name)

    def lineage_query(self, sql: str) -> pa.Table:
        """SQL query over lineage events (Story 8.3)."""
        from arrow_lake.catalog.lineage import LineageQueryBridge

        store = self._get_component(
            "lineage",
            lambda: self._create_lineage_store(),
        )

        def _create_bridge() -> LineageQueryBridge:
            return LineageQueryBridge(
                store,
                session_manager=self.get_session_manager(),
            )

        bridge = self._get_component("lineage_bridge", _create_bridge)
        return bridge.query(sql)

    def lineage_graph(self, dataset_name: str, *, max_depth: int = 10) -> dict[str, Any]:
        """Get the full lineage graph for a dataset."""
        from arrow_lake.catalog.lineage import LineageQueryBridge

        store = self._get_component(
            "lineage",
            lambda: self._create_lineage_store(),
        )

        def _create_bridge() -> LineageQueryBridge:
            return LineageQueryBridge(store, session_manager=self.get_session_manager())

        bridge = self._get_component("lineage_bridge", _create_bridge)
        return bridge.trace_full_graph(dataset_name, max_depth=max_depth)

    def lineage_impact(self, dataset_name: str) -> list[dict[str, Any]]:
        """Analyze downstream impact of changing a dataset."""
        from arrow_lake.catalog.lineage import LineageQueryBridge

        store = self._get_component(
            "lineage",
            lambda: self._create_lineage_store(),
        )

        def _create_bridge() -> LineageQueryBridge:
            return LineageQueryBridge(store, session_manager=self.get_session_manager())

        bridge = self._get_component("lineage_bridge", _create_bridge)
        return bridge.trace_impact(dataset_name)
