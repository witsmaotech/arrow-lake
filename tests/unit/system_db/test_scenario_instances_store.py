"""W3(v1.11.5)— ScenarioInstanceStore(V025 两表)。

契约(design §二):
* 实例 CRUD:create/get/list(按 scenario_id/status 过滤)/update(部分
  列;finished=True 落 finished_at);
* 步运行 upsert:start_step(running)→ finish_step(终态);UNIQUE
  (instance_id, step_id)——未启动步直接 finish(skipped/timeout)亦建行;
* 重启持久:写后显式 commit(libSQL 不 autocommit,速查坑)——新建连接
  重读全部可见;
* 孤儿回收:mark_orphaned_running 把 running 实例标 failed("orphaned
  runner"),终态实例不动。
"""

from __future__ import annotations

import json

import pytest
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.scenario_instances import ScenarioInstanceStore


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
    iid = store.create_instance(
        scenario_id="SCN.TEST",
        scenario_version=1,
        dataset="gas_net",
        object_type="alerts",
        object_id="GAS.ALERT.001",
        actor=actor,
        context_json=json.dumps({"target": {"object_id": "GAS.ALERT.001"}}),
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


def test_mark_orphaned_running(store: ScenarioInstanceStore) -> None:
    live = _create(store)  # running
    done = _create(store, status="completed")
    failed = _create(store, status="failed")
    n = store.mark_orphaned_running()
    assert n == 1  # 只有 running 被回收
    a = store.get_instance(live)
    assert a is not None and a["status"] == "failed"
    assert "orphaned" in (a["error"] or "")
    assert a["finished_at"] is not None
    assert store.get_instance(done)["status"] == "completed"
    assert store.get_instance(failed)["status"] == "failed"
