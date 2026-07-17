"""TaskHistoryStore — durable history of completed/failed background tasks.

Redis (``api/tasks.py``) holds real-time task state with a 2h TTL. This
store captures the **completed/failed** state durably so task history
remains queryable beyond the TTL and across restarts. ``TaskManager`` writes
here on completion (additive — Redis is untouched).
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from arrow_lake.system_db.connection import SystemDB
from arrow_lake.system_db.stores.base import FailMode

logger = structlog.get_logger(__name__)


class TaskHistoryStore:
    """Durable task-completion history. Fail-soft."""

    fail_mode = FailMode.FAIL_SOFT

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    def record(self, task: dict[str, Any]) -> None:
        """Upsert a task's (final or current) state into history.

        ``task`` matches ``BackgroundTask.to_dict()`` shape.
        """
        task_id = task.get("task_id")
        if not task_id:
            return
        duration_ms = None
        started = task.get("created_at") or task.get("started_at")
        completed = task.get("completed_at")
        if started and completed:
            try:
                from datetime import datetime

                s = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                c = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
                duration_ms = int((c - s).total_seconds() * 1000)
            except (ValueError, TypeError):
                pass

        with self._db.with_write() as db:
            db.execute(
                "INSERT INTO task_history "
                "(task_id, operation, dataset_name, status, progress, result, detail, "
                " error, started_at, completed_at, duration_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET "
                "status=excluded.status, progress=excluded.progress, "
                "result=excluded.result, detail=excluded.detail, error=excluded.error, "
                "completed_at=excluded.completed_at, duration_ms=excluded.duration_ms",
                (
                    str(task_id),
                    str(task.get("operation", "")),
                    str(task.get("dataset_name", "")),
                    str(task.get("status", "")),
                    float(task.get("progress", 0.0) or 0.0),
                    _dumps(task.get("result")),
                    _dumps(task.get("detail")),
                    task.get("error"),
                    started,
                    completed or None,
                    duration_ms,
                ),
            )

    def get(self, task_id: str) -> dict[str, Any] | None:
        cur = self._db.execute(
            "SELECT task_id, operation, dataset_name, status, progress, result, detail, "
            "error, started_at, completed_at, duration_ms, created_at "
            "FROM task_history WHERE task_id = ?",
            (task_id,),
        )
        row = cur.fetchone() if cur is not None else None
        return _row_to_task(row) if row is not None else None

    def list(
        self,
        *,
        operation: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT task_id, operation, dataset_name, status, progress, result, detail, "
            "error, started_at, completed_at, duration_ms, created_at "
            "FROM task_history WHERE 1=1"
        )
        params: list[Any] = []
        if operation:
            sql += " AND operation = ?"
            params.append(operation)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cur = self._db.execute(sql, tuple(params))
        rows = cur.fetchall() if cur is not None else []
        return [_row_to_task(r) for r in rows]


def _dumps(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _loads(v: Any) -> Any:
    if not v:
        return None
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return None


def _row_to_task(r: tuple) -> dict[str, Any]:
    return {
        "task_id": r[0],
        "operation": r[1],
        "dataset_name": r[2],
        "status": r[3],
        "progress": r[4],
        "result": _loads(r[5]),
        "detail": _loads(r[6]),
        "error": r[7],
        "created_at": r[8] or r[11],  # started_at, fall back to row created_at
        "completed_at": r[9],
        "duration_ms": r[10],
    }


__all__ = ["TaskHistoryStore"]
