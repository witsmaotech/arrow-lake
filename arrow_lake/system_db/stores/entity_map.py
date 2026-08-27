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
        """批量 upsert(executemany 单事务一次 commit);返回写入行数。"""
        payload = [
            (r["scope"], r["table_name"], r.get("source_system", ""),
             r["source_id"], r["object_id"])
            for r in rows
        ]
        if not payload:
            return 0
        self._db.executemany(
            """INSERT INTO entity_map
                   (scope, table_name, source_system, source_id, object_id)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(scope, table_name, source_system, source_id)
               DO UPDATE SET object_id = excluded.object_id""",
            payload,
        )
        self._db.commit()
        return len(payload)

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

    def lookup_object_ids(
        self, *, scope: str, table_name: str, source_id: str,
    ) -> list[str]:
        """反向解析(W4.3):行值(源 ID)→ 候选对象 ID 集(忽略
        source_system,确定性排序)。恰好一个 → 可解析;多个 → 调用方按
        歧义处理(不静默选第一个)。"""
        rows = self._db.execute(
            "SELECT DISTINCT object_id FROM entity_map "
            "WHERE scope = ? AND table_name = ? AND source_id = ? "
            "ORDER BY object_id",
            (scope, table_name, source_id),
        ).fetchall()
        return [str(r[0]) for r in rows]

    def lookup_object_ids_batch(
        self, *, scope: str, table_name: str, source_ids: list[str],
    ) -> dict[str, list[str]]:
        """F2(review):一次往返批量反向解析(对象聚合逐行 N+1 的治本)。

        返回 ``{source_id: [object_id, ...]}``(值去重升序);未命中的
        source_id 不出现在结果里。
        """
        out: dict[str, list[str]] = {}
        ids = sorted(set(source_ids))
        for i in range(0, len(ids), 500):  # IN 列表分片,防超长 SQL
            chunk = ids[i:i + 500]
            marks = ", ".join("?" for _ in chunk)
            rows = self._db.execute(
                f"SELECT DISTINCT source_id, object_id FROM entity_map "
                f"WHERE scope = ? AND table_name = ? AND source_id IN ({marks})",
                (scope, table_name, *chunk),
            ).fetchall()
            for sid, oid in rows:
                out.setdefault(str(sid), []).append(str(oid))
        for v in out.values():
            v.sort()
        return out

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
