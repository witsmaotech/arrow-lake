"""EntityMapStore (v1.11.1 W2.1, F2.1) — 源系统 ID → 对象 ID 映射。

身份解析顺序(§4.1):①表有契约 identifier 列 → 直取;②无/不同 → 查
``(scope, table_name, source_system, source_id)``。本表只承载 ②,由 ADMIN
显式维护(批量导入/删除),不挂摄入钩子(热路径红线)。沿 store 约定:
薄方法 over ``SystemDB.execute``;写后显式 commit(libSQL 速查坑)。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from arrow_lake.system_db.connection import SystemDB


class EntityMapStore:
    """entity_map 表的 upsert / lookup / list / delete。"""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # -- 写 ---------------------------------------------------------------

    def upsert(
        self, *, scope: str, table_name: str, source_system: str,
        source_id: str, object_id: str,
    ) -> None:
        """插入或改写一条映射(同键冲突 → 更新 object_id)。"""
        self._db.execute(
            """INSERT INTO entity_map
                   (scope, table_name, source_system, source_id, object_id)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(scope, table_name, source_system, source_id)
               DO UPDATE SET object_id = excluded.object_id""",
            (scope, table_name, source_system, source_id, object_id),
        )
        self._db.commit()

    def bulk_upsert(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """批量 upsert(单事务一次 commit);返回写入行数(幂等重跑同数)。"""
        count = 0
        for r in rows:
            self._db.execute(
                """INSERT INTO entity_map
                       (scope, table_name, source_system, source_id, object_id)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(scope, table_name, source_system, source_id)
                   DO UPDATE SET object_id = excluded.object_id""",
                (r["scope"], r["table_name"], r.get("source_system", ""),
                 r["source_id"], r["object_id"]),
            )
            count += 1
        if count:
            self._db.commit()
        return count

    def delete(
        self, *, scope: str, table_name: str, source_system: str, source_id: str,
    ) -> bool:
        deleted = self._db.execute(
            "DELETE FROM entity_map WHERE scope = ? AND table_name = ? "
            "AND source_system = ? AND source_id = ?",
            (scope, table_name, source_system, source_id),
        )
        self._db.commit()
        return bool(deleted.rowcount) if hasattr(deleted, "rowcount") else True

    # -- 读 ---------------------------------------------------------------

    def lookup(
        self, *, scope: str, table_name: str, source_system: str, source_id: str,
    ) -> str | None:
        rows = self._db.execute(
            "SELECT object_id FROM entity_map WHERE scope = ? AND table_name = ? "
            "AND source_system = ? AND source_id = ?",
            (scope, table_name, source_system, source_id),
        ).fetchall()
        return str(rows[0][0]) if rows else None

    def list_entries(
        self, *, scope: str, table_name: str | None = None, limit: int = 500,
    ) -> list[dict[str, Any]]:
        sql = ("SELECT id, scope, table_name, source_system, source_id, "
               "object_id, created_at FROM entity_map WHERE scope = ?")
        params: list[Any] = [scope]
        if table_name is not None:
            sql += " AND table_name = ?"
            params.append(table_name)
        sql += " ORDER BY table_name, source_system, source_id LIMIT ?"
        params.append(int(limit))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [
            {
                "id": r[0], "scope": r[1], "table_name": r[2],
                "source_system": r[3], "source_id": r[4],
                "object_id": r[5], "created_at": r[6],
            }
            for r in rows
        ]
