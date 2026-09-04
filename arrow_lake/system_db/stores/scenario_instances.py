"""ScenarioInstanceStore(v1.11.5 W3,S7/S8)——场景实例 + 步运行。

实例行是 SoT(runner 进程外可见的唯一真相);步行 ``UNIQUE(instance_id,
step_id)`` 的 upsert 语义支撑断点续跑(未启动步直接 finish 也建行,UI
时间线完整)。写后显式 commit(libSQL 不 autocommit,速查坑)。

接口被 runner 以 duck-typing 消费(纯逻辑测试用内存 Fake 镜像)——
改方法签名须双向同步。
"""

from __future__ import annotations

from typing import Any

from arrow_lake.system_db.connection import SystemDB

_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"

_INSTANCE_COLS = (
    "id, scenario_id, scenario_version, dataset, object_type, object_id, "
    "status, current_step, context_json, deadline_at, "
    "pending_compensation_json, error, actor, created_at, finished_at"
)


def _instance_row(r: Any) -> dict[str, Any]:
    return {
        "id": r[0],
        "scenario_id": r[1],
        "scenario_version": r[2],
        "dataset": r[3],
        "object_type": r[4],
        "object_id": r[5],
        "status": r[6],
        "current_step": r[7],
        "context_json": r[8],
        "deadline_at": r[9],
        "pending_compensation_json": r[10],
        "error": r[11],
        "actor": r[12],
        "created_at": r[13],
        "finished_at": r[14],
    }


def _step_row(r: Any) -> dict[str, Any]:
    return {
        "id": r[0],
        "instance_id": r[1],
        "step_id": r[2],
        "kind": r[3],
        "status": r[4],
        "output_json": r[5],
        "error": r[6],
        "started_at": r[7],
        "finished_at": r[8],
    }


class ScenarioInstanceStore:
    """scenario_instances / scenario_step_runs。"""

    def __init__(self, db: SystemDB) -> None:
        self._db = db

    # -- 实例写 -----------------------------------------------------------

    def create_instance(
        self,
        *,
        scenario_id: str,
        scenario_version: int,
        dataset: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        actor: str = "",
        context_json: str = "{}",
        deadline_at: str | None = None,
    ) -> int:
        cur = self._db.execute(
            "INSERT INTO scenario_instances "
            "(scenario_id, scenario_version, dataset, object_type, object_id, "
            " status, context_json, deadline_at, actor) "
            "VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)",
            (scenario_id, scenario_version, dataset, object_type, object_id,
             context_json, deadline_at, actor),
        )
        self._db.commit()
        return int(getattr(cur, "lastrowid", 0) or 0)

    def update_instance(
        self,
        instance_id: int,
        *,
        status: str | None = None,
        current_step: str | None = None,
        context_json: str | None = None,
        error: str | None = None,
        pending_compensation: list[str] | None = None,
        deadline_at: str | None = None,  # "" 清空(无 timeout 场景 resume 用)
        finished: bool = False,
        reopen: bool = False,
    ) -> bool:
        sets: list[str] = []
        params: list[Any] = []
        if status is not None:
            sets.append("status=?")
            params.append(status)
        if current_step is not None:
            sets.append("current_step=?")
            params.append(current_step)
        if context_json is not None:
            sets.append("context_json=?")
            params.append(context_json)
        if error is not None:
            sets.append("error=?")
            params.append(error)
        if pending_compensation is not None:
            import json

            sets.append("pending_compensation_json=?")
            params.append(json.dumps(pending_compensation))
        if deadline_at is not None:
            sets.append("deadline_at=?")
            params.append(deadline_at or None)
        if finished:
            sets.append(f"finished_at={_NOW}")
        if reopen:  # resume:重开终态实例
            sets.append("finished_at=NULL")
        if not sets:
            return False
        params.append(instance_id)
        cur = self._db.execute(
            f"UPDATE scenario_instances SET {', '.join(sets)} WHERE id=?", tuple(params)
        )
        self._db.commit()
        return bool(cur.rowcount) if hasattr(cur, "rowcount") else True

    def mark_orphaned_running(self) -> int:
        """启动期孤儿回收:进程重启即全部孤儿 → failed(可 resume)。"""
        cur = self._db.execute(
            f"UPDATE scenario_instances SET status='failed', "
            f"error='orphaned runner: owning worker exited', finished_at={_NOW} "
            "WHERE status='running'"
        )
        self._db.commit()
        return int(getattr(cur, "rowcount", 0) or 0)

    # -- 实例读 -----------------------------------------------------------

    def get_instance(self, instance_id: int) -> dict[str, Any] | None:
        row = self._db.execute(
            f"SELECT {_INSTANCE_COLS} FROM scenario_instances WHERE id=?",
            (instance_id,),
        ).fetchone()
        return _instance_row(row) if row is not None else None

    def list_instances(
        self,
        *,
        scenario_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        sql = f"SELECT {_INSTANCE_COLS} FROM scenario_instances"
        conds: list[str] = []
        params: list[Any] = []
        if scenario_id is not None:
            conds.append("scenario_id=?")
            params.append(scenario_id)
        if status is not None:
            conds.append("status=?")
            params.append(status)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [_instance_row(r) for r in rows]

    # -- 步运行(upsert:UNIQUE(instance_id, step_id))---------------------

    def start_step(self, instance_id: int, step_id: str, kind: str) -> None:
        import json

        self._db.execute(
            f"INSERT INTO scenario_step_runs "
            f"(instance_id, step_id, kind, status, output_json, started_at) "
            f"VALUES (?, ?, ?, 'running', ?, {_NOW}) "
            "ON CONFLICT(instance_id, step_id) DO UPDATE SET "
            "status='running', output_json='{}', error=NULL, "
            f"started_at={_NOW}",
            (instance_id, step_id, kind, json.dumps({})),
        )
        self._db.commit()

    def finish_step(
        self,
        instance_id: int,
        step_id: str,
        kind: str,
        status: str,
        *,
        output_json: str | None = None,
        error: str | None = None,
    ) -> bool:
        """终态 upsert;未启动步(网关 skipped/超时 timeout)也建行。"""
        self._db.execute(
            f"INSERT INTO scenario_step_runs "
            f"(instance_id, step_id, kind, status, output_json, error, started_at, finished_at) "
            f"VALUES (?, ?, ?, ?, ?, ?, {_NOW}, {_NOW}) "
            "ON CONFLICT(instance_id, step_id) DO UPDATE SET "
            "status=excluded.status, output_json=excluded.output_json, "
            "error=excluded.error, finished_at=excluded.finished_at",
            (instance_id, step_id, kind, status, output_json or "{}", error),
        )
        self._db.commit()
        return True

    def list_step_runs(self, instance_id: int) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, instance_id, step_id, kind, status, output_json, "
            "error, started_at, finished_at "
            "FROM scenario_step_runs WHERE instance_id=? ORDER BY id",
            (instance_id,),
        ).fetchall()
        return [_step_row(r) for r in rows]
