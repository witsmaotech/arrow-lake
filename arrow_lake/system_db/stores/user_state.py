"""UserStateStore — per-user saved queries / dashboards / favorites /
preferences / notifications (P3).

All tables reference ``users(id)``. Fail-soft: user-state is best-effort and
must never block the request path; callers fall back to empty results when the
store is absent (system_db disabled) or unreachable.
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


class UserStateStore:
    """Per-user state persistence. Fail-soft."""

    fail_mode = FailMode.FAIL_SOFT

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # saved queries
    # ------------------------------------------------------------------
    def save_query(
        self, user_id: int, name: str, query_text: str, *,
        query_type: str = "sql", dataset: str | None = None, is_public: bool = False,
    ) -> int:
        with self._db.with_write() as db:
            cur = db.execute(
                "INSERT INTO saved_queries "
                "(user_id, name, query_text, query_type, dataset, is_public) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, name, query_text, query_type, dataset, 1 if is_public else 0),
            )
            return int(cur.lastrowid) if cur is not None else 0

    def list_queries(self, user_id: int, *, include_public: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT id, user_id, name, query_text, query_type, dataset, is_public, created_at FROM saved_queries"
        if include_public:
            sql += " WHERE user_id = ? OR is_public = 1"
            params: tuple[Any, ...] = (user_id,)
        else:
            sql += " WHERE user_id = ?"
            params = (user_id,)
        sql += " ORDER BY created_at DESC"
        cur = self._db.execute(sql, params)
        rows = cur.fetchall() if cur is not None else []
        return [_row_query(r) for r in rows]

    def delete_query(self, user_id: int, query_id: int) -> bool:
        with self._db.with_write() as db:
            cur = db.execute(
                "DELETE FROM saved_queries WHERE id = ? AND user_id = ?",
                (query_id, user_id),
            )
            return cur is not None and cur.rowcount > 0

    # ------------------------------------------------------------------
    # dashboards
    # ------------------------------------------------------------------
    def save_dashboard(self, user_id: int, name: str, layout: Any) -> int:
        with self._db.with_write() as db:
            cur = db.execute(
                "INSERT INTO dashboards (user_id, name, layout) VALUES (?, ?, ?)",
                (user_id, name, _dumps(layout)),
            )
            return int(cur.lastrowid) if cur is not None else 0

    def list_dashboards(self, user_id: int) -> list[dict[str, Any]]:
        cur = self._db.execute(
            "SELECT id, name, layout, created_at FROM dashboards WHERE user_id = ? "
            "ORDER BY created_at DESC",
            (user_id,),
        )
        rows = cur.fetchall() if cur is not None else []
        return [{"id": r[0], "name": r[1], "layout": _loads(r[2]), "created_at": r[3]} for r in rows]

    def delete_dashboard(self, user_id: int, dashboard_id: int) -> bool:
        with self._db.with_write() as db:
            cur = db.execute(
                "DELETE FROM dashboards WHERE id = ? AND user_id = ?",
                (dashboard_id, user_id),
            )
            return cur is not None and cur.rowcount > 0

    # ------------------------------------------------------------------
    # favorites
    # ------------------------------------------------------------------
    def add_favorite(self, user_id: int, target_type: str, target_id: str) -> bool:
        with self._db.with_write() as db:
            cur = db.execute(
                "INSERT OR IGNORE INTO favorites (user_id, target_type, target_id) VALUES (?, ?, ?)",
                (user_id, target_type, target_id),
            )
            return cur is not None and cur.rowcount > 0

    def list_favorites(self, user_id: int) -> list[dict[str, Any]]:
        cur = self._db.execute(
            "SELECT target_type, target_id, created_at FROM favorites WHERE user_id = ? "
            "ORDER BY created_at DESC",
            (user_id,),
        )
        rows = cur.fetchall() if cur is not None else []
        return [{"target_type": r[0], "target_id": r[1], "created_at": r[2]} for r in rows]

    def remove_favorite(self, user_id: int, target_type: str, target_id: str) -> bool:
        with self._db.with_write() as db:
            cur = db.execute(
                "DELETE FROM favorites WHERE user_id = ? AND target_type = ? AND target_id = ?",
                (user_id, target_type, target_id),
            )
            return cur is not None and cur.rowcount > 0

    # ------------------------------------------------------------------
    # preferences
    # ------------------------------------------------------------------
    def get_preferences(self, user_id: int) -> dict[str, Any]:
        cur = self._db.execute(
            "SELECT preferences FROM user_preferences WHERE user_id = ?", (user_id,)
        )
        row = cur.fetchone() if cur is not None else None
        prefs = _loads(row[0]) if row else None
        return prefs or {}

    def set_preferences(self, user_id: int, preferences: dict[str, Any]) -> None:
        with self._db.with_write() as db:
            db.execute(
                "INSERT INTO user_preferences (user_id, preferences, updated_at) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(user_id) DO UPDATE SET preferences = excluded.preferences, "
                "updated_at = excluded.updated_at",
                (user_id, _dumps(preferences)),
            )

    # ------------------------------------------------------------------
    # notifications
    # ------------------------------------------------------------------
    def notify(self, user_id: int, message: str, *, kind: str = "info") -> int:
        with self._db.with_write() as db:
            cur = db.execute(
                "INSERT INTO notifications (user_id, kind, message) VALUES (?, ?, ?)",
                (user_id, kind, message),
            )
            return int(cur.lastrowid) if cur is not None else 0

    def list_notifications(self, user_id: int, *, unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT id, kind, message, read, created_at FROM notifications WHERE user_id = ?"
        params: list[Any] = [user_id]
        if unread_only:
            sql += " AND read = 0"
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cur = self._db.execute(sql, tuple(params))
        rows = cur.fetchall() if cur is not None else []
        return [{"id": r[0], "kind": r[1], "message": r[2], "read": bool(r[3]), "created_at": r[4]} for r in rows]

    def unread_count(self, user_id: int) -> int:
        cur = self._db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read = 0", (user_id,)
        )
        return int(cur.fetchone()[0]) if cur is not None else 0

    def mark_read(self, user_id: int, notification_id: int | None = None) -> int:
        sql = "UPDATE notifications SET read = 1 WHERE user_id = ?"
        params: list[Any] = [user_id]
        if notification_id is not None:
            sql += " AND id = ?"
            params.append(notification_id)
        sql += " AND read = 0"
        with self._db.with_write() as db:
            cur = db.execute(sql, tuple(params))
            return cur.rowcount if cur is not None else 0


def _row_query(r: tuple) -> dict[str, Any]:
    return {
        "id": r[0], "user_id": r[1], "name": r[2], "query_text": r[3],
        "query_type": r[4], "dataset": r[5], "is_public": bool(r[6]), "created_at": r[7],
    }


__all__ = ["UserStateStore"]
