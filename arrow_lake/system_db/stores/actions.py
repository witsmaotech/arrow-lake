"""ActionCatalogStore + IdempotencyStore(v1.11.2 MS3 W2.1,F3.3)。

版本链沿 ContractStore 模式(同 ``source_hash`` 跳过,无结构化 diff——
S5 缺口登记)。幂等去重 = UNIQUE 裁决 + owner token 归属:

* ``try_acquire``:``INSERT OR IGNORE`` 恰一行胜出;并发竞争者/已完成重放
  读到他人 owner → ``acquired=False``(中间件 → 200 已生效不重复执行);
  ``failed`` 态可被重认领(上次失败,重放可再执行)。
* 写后显式 commit(libSQL 不 autocommit,速查坑)。
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from arrow_lake.system_db.connection import SystemDB

_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"


class ActionCatalogStore:
    """actions_catalog 版本链:skip-on-same-hash。"""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # -- 写 ---------------------------------------------------------------

    def save_action(self, scope: str, action_yaml: str) -> dict[str, Any]:
        """Insert the next version unless the latest has the same hash.

        Returns ``{"id", "version", "created", "source_hash"}``.
        """
        source_hash = hashlib.sha1(action_yaml.encode("utf-8")).hexdigest()
        latest = self.get_version(scope)
        if latest is not None and latest["source_hash"] == source_hash:
            return {
                "id": latest["id"],
                "version": latest["version"],
                "created": False,
                "source_hash": source_hash,
            }
        version = (latest["version"] + 1) if latest is not None else 1
        self._db.execute(
            """INSERT INTO actions_catalog (scope, version, action_yaml, source_hash)
               VALUES (?, ?, ?, ?)""",
            (scope, version, action_yaml, source_hash),
        )
        self._db.commit()
        rows = self._db.execute(
            "SELECT id FROM actions_catalog WHERE scope = ? AND version = ?",
            (scope, version),
        ).fetchall()
        return {
            "id": int(rows[0][0]),
            "version": version,
            "created": True,
            "source_hash": source_hash,
        }

    def delete_scope(self, scope: str) -> bool:
        deleted = self._db.execute("DELETE FROM actions_catalog WHERE scope = ?", (scope,))
        self._db.commit()
        return bool(deleted.rowcount) if hasattr(deleted, "rowcount") else True

    # -- 读 ---------------------------------------------------------------

    def get_version(self, scope: str, *, version: int | None = None) -> dict[str, Any] | None:
        sql = (
            "SELECT id, scope, version, source_hash, created_at, action_yaml "
            "FROM actions_catalog WHERE scope = ?"
        )
        params: list[Any] = [scope]
        if version is None:
            sql += " ORDER BY version DESC LIMIT 1"
        else:
            sql += " AND version = ?"
            params.append(version)
        row = self._db.execute(sql, tuple(params)).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "scope": row[1],
            "version": row[2],
            "source_hash": row[3],
            "created_at": row[4],
            "action_yaml": row[5],
        }

    def list_versions(self, scope: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, scope, version, source_hash, created_at "
            "FROM actions_catalog WHERE scope = ? ORDER BY version DESC LIMIT ?",
            (scope, int(limit)),
        ).fetchall()
        return [
            {
                "id": r[0],
                "scope": r[1],
                "version": r[2],
                "source_hash": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]

    def list_scopes(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT scope, version, source_hash, created_at FROM actions_catalog "
            "WHERE version = (SELECT MAX(version) FROM actions_catalog a2 "
            "                 WHERE a2.scope = actions_catalog.scope) "
            "ORDER BY scope"
        ).fetchall()
        return [
            {"scope": r[0], "version": r[1], "source_hash": r[2], "created_at": r[3]} for r in rows
        ]


class IdempotencyStore:
    """idempotency_keys:同 (action_id, key) 单次执行(S4/M6)。"""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    def try_acquire(self, action_id: str, key: str, *, owner: str | None = None) -> dict[str, Any]:
        """Claim an execution slot.

        Returns ``{"acquired", "id", "action_id", "key", "state", "owner",
        "detail", "created_at", "updated_at"}`` — ``acquired=False`` 表示
        他人持有(running)或已完成(completed)→ 调用方 200 已生效。
        """
        token = owner or uuid4().hex
        self._db.execute(
            "INSERT OR IGNORE INTO idempotency_keys (action_id, key, state, owner) "
            "VALUES (?, ?, 'running', ?)",
            (action_id, key, token),
        )
        # failed → 重认领(上次执行失败,重放应可再执行)
        self._db.execute(
            f"UPDATE idempotency_keys SET state='running', owner=?, updated_at={_NOW} "
            "WHERE action_id=? AND key=? AND state='failed'",
            (token, action_id, key),
        )
        self._db.commit()
        rec = self.get(action_id, key)
        if rec is None:  # pragma: no cover — INSERT 即建行,防御分支
            raise RuntimeError("idempotency acquire failed to create row")
        acquired = rec["state"] == "running" and rec["owner"] == token
        return {"acquired": acquired, **rec}

    def mark(self, action_id: str, key: str, state: str, *, detail: str | None = None) -> bool:
        """Record execution outcome: running → completed | failed."""
        if state not in ("completed", "failed"):
            raise ValueError(f"state must be completed|failed, got {state!r}")
        self._db.execute(
            f"UPDATE idempotency_keys SET state=?, detail=?, updated_at={_NOW} "
            "WHERE action_id=? AND key=?",
            (state, detail, action_id, key),
        )
        self._db.commit()
        return self.get(action_id, key) is not None

    def reset_running(self, action_id: str, key: str, *, detail: str = "admin reset") -> bool:
        """ADMIN 手术:running → failed(可重认领)。

        W4.5 H-2 运维面:worker 在 acquire 与 mark 之间死亡会留永久
        running 槽(无心跳可判死,沿 tasks.py orphan 教训以人工核销兜底)。
        仅 running 态可重置;返回是否有行被重置。
        """
        cur = self._db.execute(
            f"UPDATE idempotency_keys SET state='failed', detail=?, "
            f"updated_at={_NOW} WHERE action_id=? AND key=? AND state='running'",
            (detail, action_id, key),
        )
        self._db.commit()
        return bool(cur.rowcount) if hasattr(cur, "rowcount") else True

    def get(self, action_id: str, key: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT id, action_id, key, state, owner, detail, created_at, updated_at "
            "FROM idempotency_keys WHERE action_id=? AND key=?",
            (action_id, key),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "action_id": row[1],
            "key": row[2],
            "state": row[3],
            "owner": row[4],
            "detail": row[5],
            "created_at": row[6],
            "updated_at": row[7],
        }
