"""ScenarioStore(v1.11.2 MS3 W2.2,S5)——场景版本链。

沿 ContractStore 模式:同 ``source_hash`` 跳过;内容变 → 下一版本;无
结构化 diff(与 V015/V016 同款缺口登记)。写后显式 commit。
"""

from __future__ import annotations

import hashlib
from typing import Any

from arrow_lake.system_db.connection import SystemDB


class ScenarioStore:
    """scenarios 版本链:skip-on-same-hash。"""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # -- 写 ---------------------------------------------------------------

    def save_scenario(self, scope: str, scenario_yaml: str) -> dict[str, Any]:
        """Insert the next version unless the latest has the same hash."""
        source_hash = hashlib.sha1(scenario_yaml.encode("utf-8")).hexdigest()
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
            """INSERT INTO scenarios (scope, version, scenario_yaml, source_hash)
               VALUES (?, ?, ?, ?)""",
            (scope, version, scenario_yaml, source_hash),
        )
        self._db.commit()
        rows = self._db.execute(
            "SELECT id FROM scenarios WHERE scope = ? AND version = ?",
            (scope, version),
        ).fetchall()
        return {
            "id": int(rows[0][0]),
            "version": version,
            "created": True,
            "source_hash": source_hash,
        }

    def delete_scope(self, scope: str) -> bool:
        deleted = self._db.execute("DELETE FROM scenarios WHERE scope = ?", (scope,))
        self._db.commit()
        return bool(deleted.rowcount) if hasattr(deleted, "rowcount") else True

    # -- 读 ---------------------------------------------------------------

    def get_version(self, scope: str, *, version: int | None = None) -> dict[str, Any] | None:
        sql = (
            "SELECT id, scope, version, source_hash, created_at, scenario_yaml "
            "FROM scenarios WHERE scope = ?"
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
            "scenario_yaml": row[5],
        }

    def list_versions(self, scope: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, scope, version, source_hash, created_at "
            "FROM scenarios WHERE scope = ? ORDER BY version DESC LIMIT ?",
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
            "SELECT scope, version, source_hash, created_at FROM scenarios "
            "WHERE version = (SELECT MAX(version) FROM scenarios s2 "
            "                 WHERE s2.scope = scenarios.scope) "
            "ORDER BY scope"
        ).fetchall()
        return [
            {"scope": r[0], "version": r[1], "source_hash": r[2], "created_at": r[3]} for r in rows
        ]
