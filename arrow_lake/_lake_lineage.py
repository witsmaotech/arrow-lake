"""Lineage mixin — data lineage recording, history, and querying."""

from __future__ import annotations

import queue
import threading
from typing import Any

import pyarrow as pa

# v1.9.0 async lineage capture: bounded queue + daemon worker per Lake so the
# Lance _lineage_events append + index write never blocks the ingest path.
_LINEAGE_INIT_LOCK = threading.Lock()
_LINEAGE_QUEUE_MAXSIZE = 10000


class _LakeLineageMixin:
    """Provides data lineage event recording, history, and SQL querying."""

    def _create_lineage_store(self):
        """Create a LineageStore and inject auth provider if available."""
        from arrow_lake.catalog.lineage import LineageStore

        store = LineageStore(self._get_storage())
        auth = getattr(self, "_gravitino_auth_provider", None)
        if auth is not None:
            store.set_auth_provider(auth)
        # v1.9.0: inject the libSQL lineage adjacency index when enabled.
        index = getattr(self, "_lineage_index_store", None)
        if index is not None:
            store.set_lineage_index(index)
        return store

    def _lineage_after_ingest(
        self,
        dataset_name: str,
        *,
        source_paths: list[str] | None = None,
        source_descriptor: dict[str, Any] | None = None,
        transform_type: str = "ingest",
        operation: str = "append",
        actor: str = "system",
        lance_version: int | None = None,
        total_rows: int | None = None,
    ) -> None:
        """v1.9.0 async fire-and-forget lineage capture after a successful ingest.

        Enqueues onto a bounded background worker so the Lance _lineage_events
        append + index write never adds latency to the ingest path. Best-effort:
        a full queue drops the event (lineage is reconstructable from Lance).

        v1.9.4: threads ``actor`` (who), ``lance_version`` (post-write version),
        and ``total_rows`` so lineage events carry real provenance instead of
        the prior ``actor="system"`` / empty ``source_datasets`` placeholders.
        """
        try:
            self._get_lineage_queue().put_nowait(
                (dataset_name, operation, list(source_paths or []),
                 dict(source_descriptor or {}), transform_type,
                 actor, lance_version, total_rows)
            )
        except Exception:  # noqa: BLE001 — queue full / unavailable → drop
            pass

    def _get_lineage_queue(self) -> Any:
        """Lazy-init one bounded queue + daemon worker per Lake instance."""
        q = getattr(self, "_lineage_queue", None)
        if q is not None:
            return q
        with _LINEAGE_INIT_LOCK:
            q = getattr(self, "_lineage_queue", None)
            if q is not None:
                return q
            q = queue.Queue(maxsize=_LINEAGE_QUEUE_MAXSIZE)
            self._lineage_queue = q
            worker = threading.Thread(
                target=self._lineage_worker, name="lineage-async", daemon=True
            )
            worker.start()
            return q

    def _lineage_worker(self) -> None:
        """Drain the lineage queue; record each event best-effort."""
        q = self._lineage_queue
        while True:
            try:
                (dataset_name, operation, source_paths, source_descriptor,
                 transform_type, actor, lance_version, total_rows) = q.get()
            except Exception:  # noqa: BLE001
                break
            try:
                from arrow_lake.catalog.lineage_hooks import _extract_source_datasets
                source_datasets = _extract_source_datasets(source_paths)
                meta = {"source_paths": source_paths, **source_descriptor}
                if total_rows is not None:
                    meta["total_rows"] = total_rows
                self.lineage_record_event(
                    dataset_name, operation,
                    source_datasets=source_datasets,
                    transform_type=transform_type,
                    actor=actor,
                    lance_version=lance_version,
                    metadata=meta,
                )
            except Exception:  # noqa: BLE001 — best-effort
                pass
            finally:
                q.task_done()

    def lineage_record_event(
        self,
        dataset_name: str,
        operation: str,
        *,
        source_datasets: list[str] | None = None,
        transform_type: str = "",
        actor: str = "system",
        lance_version: int | None = None,
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
            lance_version=lance_version,
            actor=actor,
            metadata=metadata,
        )
        store.record_event(event)

    def lineage_record_row(
        self,
        dataset_name: str,
        row_id: str | int,
        *,
        source_rows: list[dict[str, Any]] | None = None,
        operation: str = "derive",
        actor: str = "system",
    ) -> None:
        """Record row-level lineage using Lance native row ids (v1.8.0 #3).

        Adds row-level granularity on top of event-level lineage: tracks which
        source rows (each ``{"dataset": ..., "row_id": ...}``) produced a given
        output row, enabling precise row-level provenance / 行级溯源. Implemented
        as a lineage event tagged ``level="row"`` with the row_id + source_rows
        in metadata, so it flows through the existing lineage store/query/graph.

        Args:
            dataset_name: Output dataset.
            row_id: Lance row id of the output row.
            source_rows: List of ``{"dataset", "row_id"}`` provenance entries.
            operation: Operation that produced the row (default "derive").
            actor: Actor (default "system").
        """
        self.lineage_record_event(
            dataset_name,
            operation,
            source_datasets=[
                r["dataset"] for r in (source_rows or []) if "dataset" in r
            ]
            or None,
            transform_type="row_level",
            actor=actor,
            metadata={
                "level": "row",
                "row_id": str(row_id),
                "source_rows": source_rows or [],
            },
        )

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
