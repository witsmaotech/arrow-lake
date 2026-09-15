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

# 孤儿回收年龄阈值(秒):runner 心跳 20s 触写 updated_at,阈值对照
# api/tasks.py _ORPHAN_STALE_SECONDS=180 先例——防 sibling 重启误杀活 runner。
_ORPHAN_STALE_SECONDS = 180.0

_INSTANCE_COLS = (
    "id, scenario_id, scenario_version, dataset, object_type, object_id, "
    "status, current_step, context_json, deadline_at, "
    "pending_compensation_json, error, actor, created_at, finished_at, updated_at"
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
        "updated_at": r[15],
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
            f" status, context_json, deadline_at, actor, updated_at) "
            f"VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, {_NOW})",
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
        sets.append(f"updated_at={_NOW}")  # H-1:每次写即心跳(孤儿回收年龄锚)
        params.append(instance_id)
        cur = self._db.execute(
            f"UPDATE scenario_instances SET {', '.join(sets)} WHERE id=?", tuple(params)
        )
        self._db.commit()
        return bool(cur.rowcount) if hasattr(cur, "rowcount") else True

    def touch(self, instance_id: int) -> bool:
        """仅刷新 updated_at(runner 心跳;孤儿回收年龄锚,H-1)。"""
        cur = self._db.execute(
            f"UPDATE scenario_instances SET updated_at={_NOW} WHERE id=? AND status='running'",
            (instance_id,),
        )
        self._db.commit()
        return bool(cur.rowcount) if hasattr(cur, "rowcount") else True

    # resume CAS 可重开的终态(与 router 的 409 校验同词表)
    RESUMABLE_STATUSES = ("failed", "timeout", "compensated", "terminated")

    def resume_instance(self, instance_id: int, *, deadline_at: str | None = "") -> bool:
        """CAS 终态→running(M9,v1.11.6.6)。

        WHERE 带终态条件:并发 resume/resume(或 terminate 竞争)仅一方
        成功,0 行=状态已漂移(调用方 409);防双活 runner。
        """
        cur = self._db.execute(
            f"UPDATE scenario_instances SET status='running', error=NULL, "
            f"deadline_at=?, finished_at=NULL, updated_at={_NOW} "
            "WHERE id=? AND status IN "
            "('failed','timeout','compensated','terminated')",
            (deadline_at or None, instance_id),
        )
        self._db.commit()
        return bool(cur.rowcount) if hasattr(cur, "rowcount") else True

    def terminate_instance(self, instance_id: int) -> bool:
        """CAS running→terminated(LOW,v1.11.6.6):读-写窗口内实例自行
        completed/failed 时不覆写其终态(0 行=已漂移,调用方 409)。"""
        cur = self._db.execute(
            f"UPDATE scenario_instances SET status='terminated', "
            f"error='terminated by admin', finished_at={_NOW}, updated_at={_NOW} "
            "WHERE id=? AND status='running'",
            (instance_id,),
        )
        self._db.commit()
        return bool(cur.rowcount) if hasattr(cur, "rowcount") else True

    def mark_orphaned_running(
        self, *, stale_seconds: float = _ORPHAN_STALE_SECONDS
    ) -> int:
        """启动期孤儿回收:**超龄** running → failed(可 resume)。

        H-1(四维 review):原无条件杀全部 running,sibling worker 重启即
        误杀活 runner。改为年龄阈值——runner 心跳 20s 触写 updated_at(回
        退 created_at),阈值须 > 心跳间隔;单步执行期间心跳由 runner 内
        置任务维持(见 ScenarioRunner.run),超龄即真孤儿。
        """
        cur = self._db.execute(
            f"UPDATE scenario_instances SET status='failed', "
            f"error='orphaned runner: owning worker exited', finished_at={_NOW} "
            "WHERE status='running' "
            "AND (strftime('%s','now') - "
            "     COALESCE(strftime('%s', updated_at), strftime('%s', created_at))) > ?",
            (int(stale_seconds),),
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
        offset: int = 0,
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
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.append(int(limit))
        params.append(int(offset))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [_instance_row(r) for r in rows]

    def count_instances(
        self,
        *,
        scenario_id: str | None = None,
        status: str | None = None,
    ) -> int:
        """过滤同 list_instances 的真总数(M12:``total=len(rows)`` 只反映
        当前页,翻页后前端计数错)。"""
        sql = "SELECT COUNT(*) FROM scenario_instances"
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
        row = self._db.execute(sql, tuple(params)).fetchone()
        return int(row[0]) if row is not None else 0

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
