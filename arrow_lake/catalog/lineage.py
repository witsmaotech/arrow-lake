"""Data lineage tracking — Story 8.3.

Provides lineage event persistence and query interface for tracing
data transformations across datasets. Uses Lance for immutable
event storage and DuckDB for SQL queries over lineage data.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa
import structlog

from arrow_lake.exceptions import CatalogError, ErrorCode, StorageError
from arrow_lake.query._db import DuckDBSession
from arrow_lake.validation import DANGEROUS_SQL_KEYWORDS_RE

logger = structlog.get_logger(__name__)

__all__ = ["LineageEvent", "LineageQueryBridge", "LineageStore"]

_LINEAGE_EVENT_SCHEMA = pa.schema(
    [
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("timestamp", pa.string(), nullable=False),
        pa.field("dataset_name", pa.string(), nullable=False),
        pa.field("operation", pa.string(), nullable=False),
        pa.field("source_datasets", pa.string(), nullable=True),
        pa.field("transform_type", pa.string(), nullable=True),
        pa.field("lance_version", pa.int64(), nullable=True),
        pa.field("actor", pa.string(), nullable=True),
        pa.field("metadata", pa.string(), nullable=True),
    ]
)


@dataclass(frozen=True)
class LineageEvent:
    """A single lineage event representing a data transformation.

    Attributes:
        event_id: Unique event identifier (UUID).
        timestamp: ISO 8601 timestamp.
        dataset_name: Target dataset name.
        operation: Operation type (create/append/transform/delete).
        source_datasets: List of upstream dataset names.
        transform_type: Type of transformation applied.
        lance_version: Lance version at time of event.
        actor: Who or what triggered the event.
        metadata: Additional context as dict.
    """

    event_id: str
    timestamp: str
    dataset_name: str
    operation: str
    source_datasets: tuple[str, ...]
    transform_type: str
    lance_version: int | None
    actor: str
    metadata: tuple[tuple[str, Any], ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LineageEvent:
        """Create from a dict, normalizing mutable fields to immutable."""
        meta = data.get("metadata", {})
        if isinstance(meta, dict):
            meta = tuple(sorted(meta.items()))
        return cls(
            event_id=data["event_id"],
            timestamp=data["timestamp"],
            dataset_name=data["dataset_name"],
            operation=data["operation"],
            source_datasets=tuple(data.get("source_datasets", [])),
            transform_type=data.get("transform_type", ""),
            lance_version=data.get("lance_version"),
            actor=data.get("actor", ""),
            metadata=meta,
        )


class LineageStore:
    """Persists lineage events to a Lance dataset.

    Args:
        storage: LanceStorageManager for unified data access.
        store_dataset: Name of the lineage events dataset.
    """

    def __init__(self, storage: Any, store_dataset: str = "_lineage_events") -> None:
        self._storage = storage
        self._store_dataset = store_dataset
        self._initialized = False

    def record_event(self, event: LineageEvent) -> None:
        """Record a lineage event to the store.

        Args:
            event: LineageEvent to persist.

        Raises:
            CatalogError: If writing to Lance fails.
        """
        self._ensure_store()

        row = {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "dataset_name": event.dataset_name,
            "operation": event.operation,
            "source_datasets": json.dumps(list(event.source_datasets)),
            "transform_type": event.transform_type,
            "lance_version": event.lance_version,
            "actor": event.actor,
            "metadata": json.dumps(dict(event.metadata) if event.metadata else {}),
        }
        table = pa.table({k: [v] for k, v in row.items()}, schema=_LINEAGE_EVENT_SCHEMA)

        try:
            self._storage.append_dataset(self._store_dataset, table)
        except (OSError, StorageError) as exc:
            raise CatalogError(
                error_code=ErrorCode.LINEAGE_STORE_FAILED,
                message=f"Failed to record lineage event: {exc}",
            ) from exc

        logger.info(
            "lineage_event_recorded",
            event_id=event.event_id,
            dataset=event.dataset_name,
            operation=event.operation,
        )

    def get_dataset_history(self, dataset_name: str) -> list[LineageEvent]:
        """Get all lineage events for a dataset.

        Args:
            dataset_name: Name of the dataset.

        Returns:
            List of LineageEvent in chronological order.
        """
        self._ensure_store()

        try:
            table = self._storage.read_dataset(self._store_dataset)
        except (StorageError, OSError):
            return []

        if table.num_rows == 0:
            return []

        names = table.column("dataset_name").to_pylist()
        indices = [i for i, n in enumerate(names) if n == dataset_name]

        return [self._row_to_event(table, i) for i in indices]

    def _ensure_store(self) -> None:
        """Lazily create the lineage events dataset if it doesn't exist."""
        if self._initialized:
            return

        if not self._storage.dataset_exists(self._store_dataset):
            empty_table = pa.table(
                {f.name: [] for f in _LINEAGE_EVENT_SCHEMA},
                schema=_LINEAGE_EVENT_SCHEMA,
            )
            self._storage.create_dataset(self._store_dataset, empty_table)
            logger.info("lineage_store_created", dataset=self._store_dataset)

        self._initialized = True

    @staticmethod
    def _row_to_event(table: pa.Table, index: int) -> LineageEvent:
        """Convert a table row to LineageEvent."""
        sources = table.column("source_datasets")[index].as_py()
        meta_str = table.column("metadata")[index].as_py()
        return LineageEvent.from_dict(
            {
                "event_id": table.column("event_id")[index].as_py(),
                "timestamp": table.column("timestamp")[index].as_py(),
                "dataset_name": table.column("dataset_name")[index].as_py(),
                "operation": table.column("operation")[index].as_py(),
                "source_datasets": json.loads(sources) if sources else [],
                "transform_type": table.column("transform_type")[index].as_py() or "",
                "lance_version": table.column("lance_version")[index].as_py(),
                "actor": table.column("actor")[index].as_py() or "",
                "metadata": json.loads(meta_str) if meta_str else {},
            }
        )


class LineageQueryBridge:
    """SQL query interface over lineage events via DuckDB.

    Args:
        store: LineageStore instance.
        session_manager: Optional DuckDBSessionManager for managed connections.
    """

    def __init__(
        self,
        store: LineageStore,
        session_manager: Any | None = None,
    ) -> None:
        self._store = store
        self._session_manager = session_manager

    def query(self, sql: str, params: list[str] | None = None) -> pa.Table:
        """Execute a SQL query over lineage events.

        Args:
            sql: SQL query string (must be SELECT only).
            params: Optional parameterized query values (? placeholders).

        Returns:
            Arrow Table with query results.

        Raises:
            CatalogError: If SQL validation fails or query fails.
        """
        self._validate_sql(sql)

        try:
            table = self._store._storage.read_dataset(self._store._store_dataset)
        except (StorageError, OSError):
            table = pa.table(
                {f.name: [] for f in _LINEAGE_EVENT_SCHEMA},
                schema=_LINEAGE_EVENT_SCHEMA,
            )

        if self._session_manager is not None:
            with self._session_manager.acquire() as conn:
                conn.register(self._store._store_dataset, table)
                result_reader = conn.execute(sql, params if params else None).arrow()
                result_table = (
                    result_reader.read_all()
                    if hasattr(result_reader, "read_all")
                    else result_reader
                )
        else:
            with DuckDBSession() as conn:
                conn.register(self._store._store_dataset, table)
                result_reader = conn.execute(sql, params if params else None).arrow()
                result_table = (
                    result_reader.read_all()
                    if hasattr(result_reader, "read_all")
                    else result_reader
                )

        return result_table

    def trace_upstream(self, dataset_name: str) -> list[LineageEvent]:
        """Trace upstream dependencies of a dataset.

        Finds all events where `source_datasets` contains the target.

        Args:
            dataset_name: Name of the dataset to trace.

        Returns:
            List of LineageEvent representing upstream sources.
        """
        sql = "SELECT * FROM _lineage_events WHERE source_datasets LIKE ? ORDER BY timestamp"
        table = self.query(sql, params=[f'%"{dataset_name}"%'])
        return [LineageStore._row_to_event(table, i) for i in range(table.num_rows)]

    def trace_downstream(self, dataset_name: str) -> list[LineageEvent]:
        """Trace downstream dependents of a dataset.

        Finds all events where `dataset_name` matches and `source_datasets`
        is non-empty.

        Args:
            dataset_name: Name of the dataset to trace.

        Returns:
            List of LineageEvent representing downstream consumers.
        """
        sql = (
            "SELECT * FROM _lineage_events "
            "WHERE dataset_name = ? "
            "AND source_datasets != '[]' "
            "AND source_datasets IS NOT NULL "
            "ORDER BY timestamp"
        )
        table = self.query(sql, params=[dataset_name])
        return [LineageStore._row_to_event(table, i) for i in range(table.num_rows)]

    def trace_full_graph(self, dataset_name: str, *, max_depth: int = 10) -> dict[str, Any]:
        """Recursively trace the complete lineage graph around a dataset.

        Performs bidirectional BFS to discover all upstream and downstream
        nodes connected to the given dataset through lineage events.

        Args:
            dataset_name: Starting dataset name.
            max_depth: Maximum traversal depth (default 10).

        Returns:
            Dict with "nodes", "edges", and "stats" keys.
        """
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        visited_edges: set[tuple[str, str, str]] = set()

        # BFS queues: (dataset_name, depth)
        upstream_queue: list[tuple[str, int]] = [(dataset_name, 0)]
        downstream_queue: list[tuple[str, int]] = [(dataset_name, 0)]
        visited_upstream: set[str] = {dataset_name}
        visited_downstream: set[str] = {dataset_name}

        # Seed node
        nodes[dataset_name] = {"id": dataset_name, "depth": 0, "type": "target"}

        # Trace upstream
        while upstream_queue:
            current, depth = upstream_queue.pop(0)
            if depth >= max_depth:
                continue
            for event in self.trace_upstream(current):
                for src in event.source_datasets:
                    if src not in nodes:
                        nodes[src] = {"id": src, "depth": depth + 1, "type": "source"}
                    edge_key = (src, event.dataset_name, event.operation)
                    if edge_key not in visited_edges:
                        visited_edges.add(edge_key)
                        edges.append({
                            "from": src,
                            "to": event.dataset_name,
                            "operation": event.operation,
                            "transform_type": event.transform_type,
                        })
                    if src not in visited_upstream:
                        visited_upstream.add(src)
                        upstream_queue.append((src, depth + 1))

        # Trace downstream
        while downstream_queue:
            current, depth = downstream_queue.pop(0)
            if depth >= max_depth:
                continue
            for event in self.trace_downstream(current):
                target = event.dataset_name
                for src in event.source_datasets:
                    if src not in nodes:
                        nodes[src] = {"id": src, "depth": depth + 1, "type": "source"}
                    edge_key = (src, target, event.operation)
                    if edge_key not in visited_edges:
                        visited_edges.add(edge_key)
                        edges.append({
                            "from": src,
                            "to": target,
                            "operation": event.operation,
                            "transform_type": event.transform_type,
                        })
                if target not in nodes:
                    nodes[target] = {"id": target, "depth": depth + 1, "type": "derived"}
                if target not in visited_downstream:
                    visited_downstream.add(target)
                    downstream_queue.append((target, depth + 1))

        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "stats": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "max_depth": max((n["depth"] for n in nodes.values()), default=0),
            },
        }

    def trace_impact(self, dataset_name: str) -> list[dict[str, Any]]:
        """Analyze downstream impact of changing a dataset.

        Returns all datasets that would be affected by a change to the
        given dataset, ordered by dependency depth.

        Args:
            dataset_name: Dataset that would change.

        Returns:
            List of dicts with "dataset", "depth", "operation" keys.
        """
        impacted: list[dict[str, Any]] = []
        visited: set[str] = {dataset_name}
        queue: list[tuple[str, int]] = [(dataset_name, 0)]

        while queue:
            current, depth = queue.pop(0)
            downstream_events = self.trace_downstream(current)
            for event in downstream_events:
                target = event.dataset_name
                if target in visited:
                    continue
                visited.add(target)
                impacted.append({
                    "dataset": target,
                    "depth": depth + 1,
                    "operation": event.operation,
                    "transform_type": event.transform_type,
                })
                queue.append((target, depth + 1))

        return impacted

    @staticmethod
    def _validate_sql(sql: str) -> None:
        """Validate SQL is SELECT-only with no dangerous patterns."""
        if not sql or not sql.strip():
            raise CatalogError(
                error_code=ErrorCode.LINEAGE_QUERY_FAILED,
                message="SQL query must not be empty",
            )

        cleaned = re.sub(r"--[^\n]*", "", sql)
        cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)

        stripped = cleaned.strip().upper()
        if not stripped.startswith("SELECT"):
            raise CatalogError(
                error_code=ErrorCode.LINEAGE_QUERY_FAILED,
                message="Only SELECT queries are allowed via LineageQueryBridge",
            )

        match = DANGEROUS_SQL_KEYWORDS_RE.search(stripped)
        if match:
            raise CatalogError(
                error_code=ErrorCode.LINEAGE_QUERY_FAILED,
                message=f"Keyword '{match.group()!r}' is not allowed in lineage queries",
            )

        if ";" in cleaned:
            raise CatalogError(
                error_code=ErrorCode.LINEAGE_QUERY_FAILED,
                message="Semicolons are not allowed in lineage queries",
            )


def create_lineage_event(
    dataset_name: str,
    operation: str,
    *,
    source_datasets: list[str] | None = None,
    transform_type: str = "",
    lance_version: int | None = None,
    actor: str = "system",
    metadata: dict[str, Any] | None = None,
) -> LineageEvent:
    """Factory function to create a LineageEvent with auto-generated fields.

    Args:
        dataset_name: Target dataset name.
        operation: Operation type (create/append/transform/delete).
        source_datasets: Upstream dataset names.
        transform_type: Transformation type.
        lance_version: Lance version at time of event.
        actor: Who triggered the event.
        metadata: Additional context.

    Returns:
        LineageEvent with auto-generated event_id and timestamp.
    """
    return LineageEvent(
        event_id=str(uuid.uuid4()),
        timestamp=datetime.now(UTC).isoformat(),
        dataset_name=dataset_name,
        operation=operation,
        source_datasets=tuple(source_datasets or []),
        transform_type=transform_type,
        lance_version=lance_version,
        actor=actor,
        metadata=tuple(sorted((metadata or {}).items())),
    )
