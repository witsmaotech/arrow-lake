"""CatalogStore — durable dataset registry (replaces temp DuckDB catalog_tables).

Standalone store over the ``dataset_registry`` table. The Ray
``CatalogActor`` refactor is deferred (it is dormant under prod_minimal,
which cuts Ray); this store is the durable home for any non-Ray catalog
access path.
"""

from __future__ import annotations

import json as _json
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

    # ------------------------------------------------------------------ #
    # Container registry (DR14 W1.2): multi-table container datasets.
    # Identity is authoritative HERE, never sniffed from storage dirs (D3).
    # ------------------------------------------------------------------ #

    def register_container(self, dataset: str, tables: list[str] | None = None) -> dict[str, Any]:
        """Register a dataset as a container (upsert).

        Re-registering an existing container replaces its declared table
        list (used for reconcile); registration is idempotent.
        """
        now = datetime.now(UTC).isoformat()
        payload = _json.dumps(sorted(set(tables or [])))
        with self._db.with_write() as db:
            db.execute(
                "INSERT INTO container_registry (dataset, tables_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(dataset) DO UPDATE SET "
                "tables_json=excluded.tables_json, updated_at=excluded.updated_at",
                (dataset, payload, now, now),
            )
        return {"dataset": dataset, "tables": sorted(set(tables or []))}

    def is_container(self, dataset: str) -> bool:
        """Authoritative container-identity check (D3)."""
        cur = self._db.execute(
            "SELECT 1 FROM container_registry WHERE dataset = ?", (dataset,)
        )
        return cur is not None and cur.fetchone() is not None

    def get_container(self, dataset: str) -> dict[str, Any] | None:
        cur = self._db.execute(
            "SELECT tables_json, created_at, updated_at FROM container_registry "
            "WHERE dataset = ?",
            (dataset,),
        )
        row = cur.fetchone() if cur is not None else None
        if row is None:
            return None
        try:
            tables = _json.loads(row[0])
        except (TypeError, ValueError):
            tables = []
        return {
            "dataset": dataset, "tables": tables,
            "created_at": row[1], "updated_at": row[2],
        }

    def list_containers(self) -> list[dict[str, Any]]:
        cur = self._db.execute(
            "SELECT dataset, tables_json, created_at, updated_at "
            "FROM container_registry ORDER BY dataset"
        )
        rows = cur.fetchall() if cur is not None else []
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                tables = _json.loads(r[1])
            except (TypeError, ValueError):
                tables = []
            out.append({
                "dataset": r[0], "tables": tables,
                "created_at": r[2], "updated_at": r[3],
            })
        return out

    def set_container_tables(self, dataset: str, tables: list[str]) -> None:
        """Replace a container's declared table list (reconcile path)."""
        if not self.is_container(dataset):
            return
        now = datetime.now(UTC).isoformat()
        with self._db.with_write() as db:
            db.execute(
                "UPDATE container_registry SET tables_json = ?, updated_at = ? "
                "WHERE dataset = ?",
                (_json.dumps(sorted(set(tables))), now, dataset),
            )

    def add_container_table(self, dataset: str, table: str) -> None:
        """Record one table in a container's declared list (idempotent).

        P1-4 (review 2026-08-26, D8): the merge happens INSIDE one UPSERT
        statement (SQLite json1) — the previous get→append→set read-modify-
        write raced across workers, so two concurrent ingests into the same
        NEW container silently dropped one table's registration (control-
        plane identity drift). Falls back to the locked read-modify-write
        when json1 is unavailable.
        """
        now = datetime.now(UTC).isoformat()
        payload = _json.dumps([table])
        with self._db.with_write() as db:
            try:
                db.execute(
                    "INSERT INTO container_registry (dataset, tables_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(dataset) DO UPDATE SET "
                    "tables_json = ("
                    "  SELECT json_group_array(j.value) FROM ("
                    "    SELECT DISTINCT value FROM ("
                    "      SELECT value FROM json_each(container_registry.tables_json) "
                    "      UNION ALL SELECT value FROM json_each(excluded.tables_json)"
                    "    ) ORDER BY value"
                    "  ) j"
                    "), updated_at = excluded.updated_at",
                    (dataset, payload, now, now),
                )
                return
            except Exception:  # noqa: BLE001 — json1 missing: locked fallback
                logger.warning(
                    "catalog_add_table_json1_unavailable",
                    dataset=dataset, table=table, exc_info=True,
                )
        info = self.get_container(dataset)
        if info is None:
            self.register_container(dataset, [table])
            return
        tables = list(info["tables"])
        if table not in tables:
            tables.append(table)
            self.set_container_tables(dataset, tables)

    def drop_container_table(self, dataset: str, table: str) -> None:
        """Remove one table from a container's declared list (no-op if absent)."""
        info = self.get_container(dataset)
        if info is None:
            return
        tables = [t for t in info["tables"] if t != table]
        if len(tables) != len(info["tables"]):
            self.set_container_tables(dataset, tables)

    def unregister_container(self, dataset: str) -> bool:
        with self._db.with_write() as db:
            cur = db.execute(
                "DELETE FROM container_registry WHERE dataset = ?", (dataset,)
            )
            return cur is not None and cur.rowcount > 0


__all__ = ["CatalogStore"]
