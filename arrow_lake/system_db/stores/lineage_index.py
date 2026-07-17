"""LineageIndexStore — queryable adjacency index over Lance lineage events.

Lineage **events** stay in Lance (``_lineage_events``); this store is a thin
adjacency index (``lineage_edges``) that turns upstream/downstream traversal
from ``LIKE '%name%'`` + in-memory BFS into indexed range scans.

An event carries ``dataset_name`` (the consumer/target) and ``source_datasets``
(the inputs). Each (source → target) pair is one edge. The index is
rebuildable from Lance at any time (fail-backfill).
"""

from __future__ import annotations

from typing import Any

import structlog

from arrow_lake.system_db.connection import SystemDB
from arrow_lake.system_db.stores.base import FailMode

logger = structlog.get_logger(__name__)


class LineageIndexStore:
    """Adjacency index over lineage events. Fail-backfill on error."""

    fail_mode = FailMode.FAIL_BACKFILL

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # ------------------------------------------------------------------
    def index_event(
        self,
        *,
        event_id: str,
        target: str,
        sources: list[str],
        operation: str = "",
        transform_type: str = "",
        actor: str = "",
        lance_version: str = "",
        occurred_at: str,
    ) -> int:
        """Index one event as (source → target) edges. Returns edges inserted."""
        if not target or not sources:
            return 0
        rows = [
            (event_id, src, target, operation, transform_type, actor, lance_version, occurred_at)
            for src in sources
            if src and src != target
        ]
        if not rows:
            return 0
        before = self._db.execute("SELECT COUNT(*) FROM lineage_edges").fetchone()[0]
        with self._db.with_write() as db:
            db.executemany(
                "INSERT OR IGNORE INTO lineage_edges "
                "(event_id, src_dataset, dst_dataset, operation, transform_type, "
                " actor, lance_version, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        after = self._db.execute("SELECT COUNT(*) FROM lineage_edges").fetchone()[0]
        return int(after) - int(before)

    # ------------------------------------------------------------------
    def upstream(self, dataset: str) -> list[dict[str, Any]]:
        """Datasets that feed into ``dataset`` (edges where dst = dataset)."""
        cur = self._db.execute(
            "SELECT DISTINCT src_dataset, operation, occurred_at, event_id "
            "FROM lineage_edges WHERE dst_dataset = ? ORDER BY occurred_at DESC",
            (dataset,),
        )
        rows = cur.fetchall() if cur is not None else []
        return [
            {"dataset": r[0], "operation": r[1], "occurred_at": r[2], "event_id": r[3]}
            for r in rows
        ]

    def downstream(self, dataset: str) -> list[dict[str, Any]]:
        """Datasets that ``dataset`` feeds into (edges where src = dataset)."""
        cur = self._db.execute(
            "SELECT DISTINCT dst_dataset, operation, occurred_at, event_id "
            "FROM lineage_edges WHERE src_dataset = ? ORDER BY occurred_at DESC",
            (dataset,),
        )
        rows = cur.fetchall() if cur is not None else []
        return [
            {"dataset": r[0], "operation": r[1], "occurred_at": r[2], "event_id": r[3]}
            for r in rows
        ]

    def neighbors(self, dataset: str) -> dict[str, list[dict[str, Any]]]:
        """Both directions in one call (for BFS seeding)."""
        return {"upstream": self.upstream(dataset), "downstream": self.downstream(dataset)}

    def edge_count(self) -> int:
        cur = self._db.execute("SELECT COUNT(*) FROM lineage_edges")
        return int(cur.fetchone()[0]) if cur is not None else 0

    def clear(self) -> int:
        with self._db.with_write() as db:
            cur = db.execute("DELETE FROM lineage_edges")
            return cur.rowcount if cur is not None else 0


__all__ = ["LineageIndexStore"]
