"""IngestDLQStore — shared dead-letter queue (replaces per-node JSONL file).

Multi-worker / multi-node safe: every worker shares one libSQL table.
Returns dicts in the same shape as ``DeadLetterItem.to_dict()`` so callers
stay compatible with the existing dataclass. Fail-soft: a store hiccup
falls back to logging rather than blocking ingestion.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog

from arrow_lake.system_db.connection import SystemDB
from arrow_lake.system_db.stores.base import FailMode

logger = structlog.get_logger(__name__)


class IngestDLQStore:
    """Durable, shared ingest dead-letter queue. Fail-soft."""

    fail_mode = FailMode.FAIL_SOFT

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # ------------------------------------------------------------------
    def add(
        self,
        file_path: str,
        error: str,
        *,
        dataset: str = "",
        metadata: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        meta_json = json.dumps(metadata) if metadata else None
        with self._db.with_write() as db:
            # Upsert: re-adding an existing (file_path, dataset) bumps the
            # attempt counter and records the latest failure.
            db.execute(
                "INSERT INTO ingest_dead_letter "
                "(file_path, dataset, status, error, last_error, attempt_count, "
                " max_retries, metadata, first_failed_at, last_failed_at) "
                "VALUES (?, ?, 'pending', ?, ?, 1, ?, ?, ?, ?) "
                "ON CONFLICT(file_path, dataset) DO UPDATE SET "
                "status='pending', last_error=excluded.last_error, "
                "attempt_count=ingest_dead_letter.attempt_count + 1, "
                "last_failed_at=excluded.last_failed_at",
                (file_path, dataset, error, error, max_retries, meta_json, now, now),
            )
        return self._get(file_path, dataset)  # type: ignore[return-value]

    def retry(self, file_path: str, *, dataset: str = "") -> bool:
        row = self._get(file_path, dataset)
        if row is None or not _can_retry(row):
            return False
        now = datetime.now(UTC).isoformat()
        with self._db.with_write() as db:
            db.execute(
                "UPDATE ingest_dead_letter SET status='retrying', "
                "attempt_count=attempt_count+1, last_failed_at=? "
                "WHERE file_path=? AND dataset=?",
                (now, file_path, dataset),
            )
        return True

    def resolve(self, file_path: str, *, dataset: str = "") -> bool:
        return self._set_status(file_path, dataset, "resolved", exclude="resolved")

    def mark_permanent(self, file_path: str, *, dataset: str = "", reason: str = "") -> bool:
        now = datetime.now(UTC).isoformat()
        with self._db.with_write() as db:
            cur = db.execute(
                "UPDATE ingest_dead_letter SET status='permanent', last_error=?, "
                "last_failed_at=? WHERE file_path=? AND dataset=?",
                (reason or "", now, file_path, dataset),
            )
            return cur is not None and cur.rowcount > 0

    def list_items(
        self, *, status: str | None = None, dataset: str | None = None
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT file_path, dataset, status, error, last_error, attempt_count, "
            "max_retries, metadata, first_failed_at, last_failed_at "
            "FROM ingest_dead_letter WHERE 1=1"
        )
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if dataset:
            sql += " AND dataset = ?"
            params.append(dataset)
        sql += " ORDER BY first_failed_at DESC"
        cur = self._db.execute(sql, tuple(params))
        rows = cur.fetchall() if cur is not None else []
        return [_row_to_item(r) for r in rows]

    def purge(self, *, resolved: bool = False, permanent: bool = False) -> int:
        if not resolved and not permanent:
            return 0
        clauses: list[str] = []
        params: list[Any] = []
        if resolved:
            clauses.append("status = 'resolved'")
        if permanent:
            clauses.append("status = 'permanent'")
        sql = f"DELETE FROM ingest_dead_letter WHERE {' OR '.join(clauses)}"
        with self._db.with_write() as db:
            cur = db.execute(sql, tuple(params))
            return cur.rowcount if cur is not None else 0

    @property
    def stats(self) -> dict[str, int]:
        cur = self._db.execute(
            "SELECT status, COUNT(*) FROM ingest_dead_letter GROUP BY status"
        )
        rows = cur.fetchall() if cur is not None else []
        counts: dict[str, int] = {r[0]: int(r[1]) for r in rows}
        counts["total"] = sum(counts.values())
        return counts

    # ------------------------------------------------------------------
    def _get(self, file_path: str, dataset: str) -> dict[str, Any] | None:
        cur = self._db.execute(
            "SELECT file_path, dataset, status, error, last_error, attempt_count, "
            "max_retries, metadata, first_failed_at, last_failed_at "
            "FROM ingest_dead_letter WHERE file_path=? AND dataset=?",
            (file_path, dataset),
        )
        row = cur.fetchone() if cur is not None else None
        return _row_to_item(row) if row is not None else None

    def _set_status(
        self, file_path: str, dataset: str, status: str, *, exclude: str | None = None
    ) -> bool:
        sql = "UPDATE ingest_dead_letter SET status=? WHERE file_path=? AND dataset=?"
        params: list[Any] = [status, file_path, dataset]
        if exclude:
            sql += " AND status <> ?"
            params.append(exclude)
        with self._db.with_write() as db:
            cur = db.execute(sql, tuple(params))
            return cur is not None and cur.rowcount > 0


def _can_retry(row: dict[str, Any]) -> bool:
    return (
        row["status"] in ("pending", "retrying")
        and row["attempt_count"] < row["max_retries"]
    )


def _row_to_item(r: tuple) -> dict[str, Any]:
    meta = r[7]
    try:
        metadata = json.loads(meta) if meta else {}
    except (ValueError, TypeError):
        metadata = {}
    return {
        "file_path": r[0],
        "dataset": r[1],
        "status": r[2],
        "error": r[3],
        "last_error": r[4],
        "attempt_count": int(r[5]),
        "max_retries": int(r[6]),
        "metadata": metadata,
        "first_failed_at": r[8],
        "last_failed_at": r[9],
    }


__all__ = ["IngestDLQStore"]
