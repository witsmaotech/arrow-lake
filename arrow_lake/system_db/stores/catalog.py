"""CatalogStore — durable dataset registry (replaces temp DuckDB catalog_tables).

Standalone store over the ``dataset_registry`` table. The Ray
``CatalogActor`` refactor is deferred (it is dormant under prod_minimal,
which cuts Ray); this store is the durable home for any non-Ray catalog
access path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from arrow_lake.system_db.connection import SystemDB
from arrow_lake.system_db.stores.base import FailMode

logger = structlog.get_logger(__name__)


class CatalogStore:
    """Dataset registry persistence. Fail-soft (non-security data)."""

    fail_mode = FailMode.FAIL_SOFT

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    def register_table(
        self, name: str, schema_json: str, location: str
    ) -> dict[str, str]:
        now = datetime.now(UTC).isoformat()
        with self._db.with_write() as db:
            db.execute(
                "INSERT INTO dataset_registry (name, schema_json, location, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'active', ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "schema_json=excluded.schema_json, location=excluded.location, "
                "status='active', updated_at=excluded.updated_at",
                (name, schema_json, location, now, now),
            )
        return {
            "name": name,
            "schema_json": schema_json,
            "location": location,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }

    def get_table(self, name: str) -> dict[str, Any] | None:
        cur = self._db.execute(
            "SELECT name, schema_json, location, status, created_at, updated_at, "
            "lance_version, row_count, size_bytes, tags "
            "FROM dataset_registry WHERE name = ?",
            (name,),
        )
        row = cur.fetchone() if cur is not None else None
        if row is None:
            return None
        return {
            "name": row[0], "schema_json": row[1], "location": row[2],
            "status": row[3], "created_at": row[4], "updated_at": row[5],
            "lance_version": row[6], "row_count": row[7],
            "size_bytes": row[8], "tags": row[9],
        }

    def list_tables(self, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = (
            "SELECT name, schema_json, location, status, created_at, updated_at "
            "FROM dataset_registry"
        )
        if not include_archived:
            sql += " WHERE status = 'active'"
        sql += " ORDER BY name"
        cur = self._db.execute(sql)
        rows = cur.fetchall() if cur is not None else []
        return [
            {"name": r[0], "schema_json": r[1], "location": r[2],
             "status": r[3], "created_at": r[4], "updated_at": r[5]}
            for r in rows
        ]

    def delete_table(self, name: str) -> bool:
        with self._db.with_write() as db:
            cur = db.execute("DELETE FROM dataset_registry WHERE name = ?", (name,))
            return cur is not None and cur.rowcount > 0

    def _set_status(self, name: str, status: str) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._db.with_write() as db:
            cur = db.execute(
                "UPDATE dataset_registry SET status = ?, updated_at = ? WHERE name = ?",
                (status, now, name),
            )
            return cur is not None and cur.rowcount > 0

    def archive_dataset(self, name: str) -> bool:
        return self._set_status(name, "archived")

    def restore_dataset(self, name: str) -> bool:
        return self._set_status(name, "active")


__all__ = ["CatalogStore"]
