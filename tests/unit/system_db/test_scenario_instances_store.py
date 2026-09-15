"""W3(v1.11.5)— ScenarioInstanceStore(V025 两表)。

契约(design §二):
* 实例 CRUD:create/get/list(按 scenario_id/status 过滤)/update(部分
  列;finished=True 落 finished_at);
* 步运行 upsert:start_step(running)→ finish_step(终态);UNIQUE
  (instance_id, step_id)——未启动步直接 finish(skipped/timeout)亦建行;
* 重启持久:写后显式 commit(libSQL 不 autocommit,速查坑)——新建连接
  重读全部可见;
* 孤儿回收(H-1,v1.11.6.6):mark_orphaned_running 把**超龄** running 实例
  标 failed("orphaned runner")——年龄锚=updated_at(回退 created_at),
  runner 心跳 20s 触写;新 running 与终态实例不动(防 sibling 重启误杀)。
"""

from __future__ import annotations

import itertools
import json

import pytest
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.scenario_instances import ScenarioInstanceStore

# V028(收敛):同 (scenario,dataset,object) 至多一条 running——多实例
# 用例的 object_id 须唯一(首条保持 GAS.ALERT.001 不破坏既有断言)。
_seq = itertools.count(1)


@pytest.fixture(autouse=True)
def _reset_seq() -> None:
    global _seq
    _seq = itertools.count(1)


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


@pytest.fixture
def store(db: SystemDB) -> ScenarioInstanceStore:
    return ScenarioInstanceStore(db)


def _create(store: ScenarioInstanceStore, *, actor: str = "op", status: str | None = None) -> int:
    object_id = f"GAS.ALERT.{next(_seq):03d}"
    iid = store.create_instance(
        scenario_id="SCN.TEST",
        scenario_version=1,
        dataset="gas_net",
        object_type="alerts",
        object_id=object_id,
        actor=actor,
        context_json=json.dumps({"target": {"object_id": object_id}}),
        deadline_at="2030-01-01T00:00:00Z",
    )
    if status is not None:
        store.update_instance(iid, status=status, finished=True)
    return iid


def test_create_and_get_roundtrip(store: ScenarioInstanceStore) -> None:
    iid = _create(store)
    rec = store.get_instance(iid)
    assert rec is not None
    assert rec["scenario_id"] == "SCN.TEST" and rec["scenario_version"] == 1
    assert rec["status"] == "running"
    assert rec["dataset"] == "gas_net" and rec["object_id"] == "GAS.ALERT.001"
    assert rec["actor"] == "op"
    assert rec["finished_at"] is None
    assert json.loads(rec["context_json"])["target"]["object_id"] == "GAS.ALERT.001"


def test_get_missing_returns_none(store: ScenarioInstanceStore) -> None:
    assert store.get_instance(9999) is None


def test_list_instances_filters(store: ScenarioInstanceStore) -> None:
    a = _create(store)
    b = _create(store)
    _create(store, actor="op2")  # 同场景第三实例(limit 语义用)
    all_running = store.list_instances(scenario_id="SCN.TEST", status="running")
    assert len(all_running) == 3  # newest first
    store.update_instance(a, status="failed", error="boom", finished=True)
    failed = store.list_instances(scenario_id="SCN.TEST", status="failed")
    assert [r["id"] for r in failed] == [a]
    assert failed[0]["error"] == "boom" and failed[0]["finished_at"] is not None
    limited = store.list_instances(limit=2)
    assert len(limited) == 2


def test_update_instance_partial_and_pending_compensation(store: ScenarioInstanceStore) -> None:
    iid = _create(store)
    ok = store.update_instance(
        iid,
        status="compensated",
        current_step="act_pub",
        pending_compensation=["ACT.UNPUB"],
        finished=True,
    )
    assert ok
    rec = store.get_instance(iid)
    assert rec is not None
    assert rec["status"] == "compensated"
    assert rec["current_step"] == "act_pub"
    assert json.loads(rec["pending_compensation_json"]) == ["ACT.UNPUB"]
    assert rec["finished_at"] is not None


def test_step_run_upsert_lifecycle(store: ScenarioInstanceStore) -> None:
    iid = _create(store)
    store.start_step(iid, "assess1", "assess")
    runs = store.list_step_runs(iid)
    assert [r["step_id"] for r in runs] == ["assess1"]
    assert runs[0]["status"] == "running" and runs[0]["kind"] == "assess"

    store.finish_step(iid, "assess1", "assess", "succeeded",
                      output_json='{"matched_rules": 2}')
    store.start_step(iid, "act_a", "action")
    store.finish_step(iid, "act_a", "action", "failed", error="x")
    # 未启动步直接 finish(skipped/timeout)也建行(UI 时间线完整)
    store.finish_step(iid, "notify", "action", "skipped")

    runs = {r["step_id"]: r for r in store.list_step_runs(iid)}
    assert set(runs) == {"assess1", "act_a", "notify"}
    assert runs["assess1"]["status"] == "succeeded"
    assert json.loads(runs["assess1"]["output_json"])["matched_rules"] == 2
    assert runs["act_a"]["status"] == "failed" and runs["act_a"]["error"] == "x"
    assert runs["notify"]["status"] == "skipped"
    assert runs["notify"]["finished_at"] is not None


def test_step_runs_scoped_per_instance(store: ScenarioInstanceStore) -> None:
    a, b = _create(store), _create(store)
    store.start_step(a, "act_a", "action")
    assert [r["step_id"] for r in store.list_step_runs(b)] == []


def test_restart_persistence_file_db(tmp_path) -> None:
    from arrow_lake.system_db import Migrator, SystemDB
    from arrow_lake.system_db.stores.scenario_instances import ScenarioInstanceStore

    path = str(tmp_path / "sys.db")
    db1 = SystemDB(f"file:{path}")
    Migrator(db1).run()
    store1 = ScenarioInstanceStore(db1)
    iid = store1.create_instance(
        scenario_id="SCN.P", scenario_version=1, actor="op", context_json="{}"
    )
    store1.start_step(iid, "act_a", "action")
    store1.finish_step(iid, "act_a", "action", "succeeded")
    store1.update_instance(iid, status="completed", finished=True)
    db1.close()

    db2 = SystemDB(f"file:{path}")
    Migrator(db2).run()
    store2 = ScenarioInstanceStore(db2)
    rec = store2.get_instance(iid)
    assert rec is not None and rec["status"] == "completed"
    assert rec["finished_at"] is not None
    runs = store2.list_step_runs(iid)
    assert [r["status"] for r in runs] == ["succeeded"]
    db2.close()


def test_mark_orphaned_running_only_stale(
    store: ScenarioInstanceStore, db: SystemDB
) -> None:
    stale = _create(store)  # running 但超龄(伪造 updated_at,真孤儿)
    db.execute(
        "UPDATE scenario_instances SET updated_at='2020-01-01T00:00:00Z' WHERE id=?",
        (stale,),
    )
    db.commit()
    fresh = _create(store)  # running 且新(runner 心跳维持 → sibling 重启不误杀)
    done = _create(store, status="completed")
    failed = _create(store, status="failed")
    n = store.mark_orphaned_running()
    assert n == 1  # 只有超龄 running 被回收
    a = store.get_instance(stale)
    assert a is not None and a["status"] == "failed"
    assert "orphaned" in (a["error"] or "")
    assert a["finished_at"] is not None
    assert store.get_instance(fresh)["status"] == "running"
    assert store.get_instance(done)["status"] == "completed"
    assert store.get_instance(failed)["status"] == "failed"


def test_mark_orphaned_running_stale_threshold(
    store: ScenarioInstanceStore, db: SystemDB
) -> None:
    # 阈值参数:updated_at 落在 10 秒前——大阈值内不回收,小于年龄即回收
    iid = _create(store)
    db.execute(
        "UPDATE scenario_instances SET updated_at=datetime('now', '-10 seconds') "
        "WHERE id=?",
        (iid,),
    )
    db.commit()
    assert store.mark_orphaned_running(stale_seconds=3600) == 0
    assert store.get_instance(iid)["status"] == "running"
    assert store.mark_orphaned_running(stale_seconds=5) == 1
    assert store.get_instance(iid)["status"] == "failed"


def test_touch_refreshes_updated_at_running_only(
    store: ScenarioInstanceStore, db: SystemDB
) -> None:
    iid = _create(store)
    db.execute(
        "UPDATE scenario_instances SET updated_at='2020-01-01T00:00:00Z' WHERE id=?",
        (iid,),
    )
    db.commit()
    assert store.touch(iid) is True
    rec = store.get_instance(iid)
    assert rec is not None and (rec["updated_at"] or "") > "2020-"
    # 终态实例不 touch(心跳不会刷新已终止实例的年龄)
    store.update_instance(iid, status="completed", finished=True)
    assert store.touch(iid) is False


# ── M9/M12(v1.11.6.6)-------------------------------------------------------


def test_resume_instance_cas(store: ScenarioInstanceStore) -> None:
    """M9:resume CAS——终态开一次成功;running 态再开(并发双击)False。"""
    iid = _create(store, status="failed")
    assert store.resume_instance(iid) is True
    rec = store.get_instance(iid)
    assert rec is not None
    assert rec["status"] == "running" and rec["error"] is None
    assert rec["finished_at"] is None
    # 已 running(第二个并发 resume 读到的旧快照)→ 0 行,False,不翻状态
    assert store.resume_instance(iid) is False
    assert store.get_instance(iid)["status"] == "running"
    # 其他终态档可开;deadline_at 重算落地("" → NULL)
    store.update_instance(iid, status="terminated", finished=True)
    assert store.resume_instance(iid, deadline_at="2030-01-01T00:00:00Z") is True
    assert store.get_instance(iid)["deadline_at"] == "2030-01-01T00:00:00Z"


def test_list_instances_offset_and_count(store: ScenarioInstanceStore) -> None:
    """M12:offset 翻页 + count_instances 真 total(过滤同 list)。"""
    ids = [_create(store) for _ in range(5)]
    store.update_instance(ids[0], status="completed", finished=True)
    page1 = store.list_instances(limit=2, offset=0)
    page2 = store.list_instances(limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    assert page1[0]["id"] > page2[0]["id"]  # id DESC
    all_rows = store.list_instances(limit=10)
    assert [r["id"] for r in all_rows[:2]] == [r["id"] for r in page1]
    assert [r["id"] for r in all_rows[2:4]] == [r["id"] for r in page2]
    assert store.count_instances() == 5
    assert store.count_instances(status="running") == 4
    assert store.count_instances(scenario_id="SCN.TEST", status="completed") == 1


def test_terminate_instance_cas(store: ScenarioInstanceStore) -> None:
    """LOW(v1.11.6.6):CAS running→terminated;非 running(读-写窗口内
    自行终态)不覆写。"""
    iid = _create(store)  # running
    assert store.terminate_instance(iid) is True
    rec = store.get_instance(iid)
    assert rec is not None
    assert rec["status"] == "terminated" and "terminated" in (rec["error"] or "")
    assert rec["finished_at"] is not None
    # 已终态再 terminate(并发竞争)→ False,原终态不被覆写
    assert store.terminate_instance(iid) is False
    assert store.get_instance(iid)["status"] == "terminated"
    done = _create(store, status="completed")
    assert store.terminate_instance(done) is False  # completed 不覆写
    assert store.get_instance(done)["status"] == "completed"


def test_v028_running_unique_per_object(store: ScenarioInstanceStore) -> None:
    """V028(收敛):同 (scenario,dataset,object) 至多一条 running——
    部分唯一索引在 DB 层封死 TOCTOU 双活;终态后可再建(resume/instantiate)。"""
    iid = _create(store)  # GAS.ALERT.001 running
    with pytest.raises(Exception, match="UNIQUE"):
        store.create_instance(
            scenario_id="SCN.TEST", scenario_version=1, dataset="gas_net",
            object_type="alerts", object_id="GAS.ALERT.001",
            context_json="{}",
        )
    # 终态释放:failed 后同对象可再 instantiate
    store.update_instance(iid, status="failed", finished=True)
    iid2 = store.create_instance(
        scenario_id="SCN.TEST", scenario_version=1, dataset="gas_net",
        object_type="alerts", object_id="GAS.ALERT.001", context_json="{}",
    )
    assert store.get_instance(iid2)["status"] == "running"


def test_exists_running_pushdown(store: ScenarioInstanceStore) -> None:
    """性能 H-2(收敛):查重 EXISTS 下推——命中返 {id},非同对象/终态返 None。"""
    iid = _create(store)  # GAS.ALERT.001 running
    assert store.exists_running(
        scenario_id="SCN.TEST", dataset="gas_net", object_type="alerts",
        object_id="GAS.ALERT.001",
    ) == {"id": iid}
    assert store.exists_running(
        scenario_id="SCN.TEST", dataset="gas_net", object_type="alerts",
        object_id="GAS.ALERT.999",
    ) is None
    store.update_instance(iid, status="failed", finished=True)
    assert store.exists_running(
        scenario_id="SCN.TEST", dataset="gas_net", object_type="alerts",
        object_id="GAS.ALERT.001",
    ) is None


def test_list_instances_context_slim(store: ScenarioInstanceStore) -> None:
    """性能 H-1(收敛):include_context=False 不取 context_json(NULL 占位)。"""
    _create(store)
    rows = store.list_instances(include_context=False)
    assert rows and rows[0]["context_json"] is None
    assert rows[0]["dataset"] == "gas_net"  # 其余列不受占位影响
    full = store.list_instances(include_context=True)
    assert full and full[0]["context_json"]  # 全列形态照旧


def test_ack_pending_cas(store: ScenarioInstanceStore) -> None:
    """M8(收敛):CAS 核销——expect_json 钉旧值,并发变更 → False。"""
    iid = _create(store)
    store.update_instance(iid, pending_compensation=["comp_a", "comp_b"])
    old = store.get_instance(iid)["pending_compensation_json"]
    ok = store.ack_pending(iid, expect_json=old, remaining=["comp_b"])
    assert ok is True
    assert json.loads(store.get_instance(iid)["pending_compensation_json"]) == ["comp_b"]
    # 旧值重放(并发窗口)→ 0 行 False,不复活已清项
    assert store.ack_pending(iid, expect_json=old, remaining=[]) is False
    assert json.loads(store.get_instance(iid)["pending_compensation_json"]) == ["comp_b"]
