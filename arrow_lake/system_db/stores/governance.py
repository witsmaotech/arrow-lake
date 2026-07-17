"""GovernanceStore — schema change / maintenance / schedule / config history.

P2 governance persistence. Each concern is a small set of append-only
(or upsert-for schedules) records. Standalone for now; wiring into
``schema.py`` / ``maintenance_scheduler.py`` / ``schedule.py`` /
``cli/config_cmd.py`` is a follow-up (the domain objects keep their current
in-memory behavior when no store is injected).
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from arrow_lake.system_db.connection import SystemDB
from arrow_lake.system_db.stores.base import FailMode

logger = structlog.get_logger(__name__)


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


class GovernanceStore:
    """Schema/maintenance/schedule/config governance history. Fail-soft."""

    fail_mode = FailMode.FAIL_SOFT

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # schema changelog
    # ------------------------------------------------------------------
    def record_schema_change(
        self,
        dataset_name: str,
        change_type: str,
        *,
        from_schema: Any = None,
        to_schema: Any = None,
        details: Any = None,
        actor: str = "",
    ) -> None:
        with self._db.with_write() as db:
            db.execute(
                "INSERT INTO schema_changelog "
                "(dataset_name, change_type, from_schema, to_schema, details, actor) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    dataset_name, change_type,
                    _dumps(from_schema), _dumps(to_schema),
                    _dumps(details), actor,
                ),
            )

    def list_schema_changes(
        self, dataset_name: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT dataset_name, change_type, from_schema, to_schema, details, actor, occurred_at "
            "FROM schema_changelog"
        )
        params: list[Any] = []
        if dataset_name:
            sql += " WHERE dataset_name = ?"
            params.append(dataset_name)
        sql += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        cur = self._db.execute(sql, tuple(params))
        rows = cur.fetchall() if cur is not None else []
        return [
            {"dataset_name": r[0], "change_type": r[1], "from_schema": _loads(r[2]),
             "to_schema": _loads(r[3]), "details": _loads(r[4]), "actor": r[5],
             "occurred_at": r[6]}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # maintenance runs
    # ------------------------------------------------------------------
    def record_maintenance_run(
        self, task_type: str, status: str, *, report: Any = None,
        started_at: str | None = None,
    ) -> None:
        with self._db.with_write() as db:
            db.execute(
                "INSERT INTO maintenance_runs (task_type, status, report, started_at) "
                "VALUES (?, ?, ?, ?)",
                (task_type, status, _dumps(report), started_at),
            )

    def list_maintenance_runs(
        self, task_type: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        sql = "SELECT task_type, status, report, started_at, completed_at FROM maintenance_runs"
        params: list[Any] = []
        if task_type:
            sql += " WHERE task_type = ?"
            params.append(task_type)
        sql += " ORDER BY completed_at DESC LIMIT ?"
        params.append(limit)
        cur = self._db.execute(sql, tuple(params))
        rows = cur.fetchall() if cur is not None else []
        return [
            {"task_type": r[0], "status": r[1], "report": _loads(r[2]),
             "started_at": r[3], "completed_at": r[4]}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # schedules
    # ------------------------------------------------------------------
    def upsert_schedule(
        self, name: str, cron_expr: str, task_kind: str,
        *, params: Any = None, enabled: bool = True,
    ) -> None:
        with self._db.with_write() as db:
            db.execute(
                "INSERT INTO schedules (name, cron_expr, task_kind, params, enabled) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "cron_expr=excluded.cron_expr, task_kind=excluded.task_kind, "
                "params=excluded.params, enabled=excluded.enabled",
                (name, cron_expr, task_kind, _dumps(params), 1 if enabled else 0),
            )

    def list_schedules(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT name, cron_expr, task_kind, params, enabled, created_at FROM schedules"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY name"
        cur = self._db.execute(sql)
        rows = cur.fetchall() if cur is not None else []
        return [
            {"name": r[0], "cron_expr": r[1], "task_kind": r[2], "params": _loads(r[3]),
             "enabled": bool(r[4]), "created_at": r[5]}
            for r in rows
        ]

    def delete_schedule(self, name: str) -> bool:
        with self._db.with_write() as db:
            cur = db.execute("DELETE FROM schedules WHERE name = ?", (name,))
            return cur is not None and cur.rowcount > 0

    # ------------------------------------------------------------------
    # config changelog
    # ------------------------------------------------------------------
    def record_config_change(
        self, key: str, *, old_value: Any = None, new_value: Any = None, actor: str = "",
    ) -> None:
        with self._db.with_write() as db:
            db.execute(
                "INSERT INTO config_changelog (key, old_value, new_value, actor) "
                "VALUES (?, ?, ?, ?)",
                (
                    key,
                    old_value if isinstance(old_value, str) else _dumps(old_value),
                    new_value if isinstance(new_value, str) else _dumps(new_value),
                    actor,
                ),
            )

    def list_config_changes(self, key: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT key, old_value, new_value, actor, occurred_at FROM config_changelog"
        params: list[Any] = []
        if key:
            sql += " WHERE key = ?"
            params.append(key)
        sql += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        cur = self._db.execute(sql, tuple(params))
        rows = cur.fetchall() if cur is not None else []
        return [
            {"key": r[0], "old_value": r[1], "new_value": r[2], "actor": r[3],
             "occurred_at": r[4]}
            for r in rows
        ]


__all__ = ["GovernanceStore"]
