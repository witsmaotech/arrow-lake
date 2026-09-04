"""W3(v1.11.5)— /api/v1/actions/scenarios 执行五端点(S7/S8/S9)。

vertical-slice 真栈(沿 test_ms3_vertical_slice 先例):真 Lake(LOCAL
hermetic)+ 真 Stores(:memory: system_db)+ 真 runner/八步中间件——
instantiate 202 → 后台跑 → 轮询终态。资产 = testing/ms3_demo.py。

契约(docs_offline/v1115-w3-scenario-runner-design.md §三):
* instantiate:EDITOR;404 场景不存在;422 引用悬空/entries 不匹配/对象
  不存在;202 {instance_id} 后台执行;
* 执行语义经真中间件:XOR 双臂(D001 高压 → publish+notify;D002 低压 →
  escalate,publish 级联 skipped);update_lifecycle 真写;审计带
  scenario/step 归属;
* instances 列表 VIEWER(过滤);详情含 step_runs+解码 context/pending;
* terminate ADMIN(非 running 409);resume EDITOR(failed 修目录后续跑至
  completed;completed/running 409);
* 补偿端到端:guard 失败 + compensation 声明 → compensated + 人工待办。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pyarrow as pa
import pytest
from arrow_lake import Lake
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.config import ArrowLakeConfig, StorageBackend, StorageConfig
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.actions import (
    ActionCatalogStore,
    IdempotencyStore,
)
from arrow_lake.system_db.stores.contracts import ContractStore
from arrow_lake.system_db.stores.identity import IdentityStore
from arrow_lake.system_db.stores.ontology import OntologyRulesStore
from arrow_lake.system_db.stores.scenario_instances import ScenarioInstanceStore
from arrow_lake.system_db.stores.scenarios import ScenarioStore
from arrow_lake.system_db.stores.semantic_alignments import SemanticAlignmentStore
from arrow_lake.system_db.stores.user_state import UserStateStore
from arrow_lake.testing import ms3_demo as demo
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

# guard 失败(补偿端到端用):to_state 不在 lifecycle 词表 → 422 ActionError
ACT_GUARD_FAIL = """
action_id: DEMO.ACT.GUARDFAIL
title: 会撞词表守卫
target: {dataset: demo_ms3_alerts, object_class: 告警事件}
effect: {type: update_lifecycle, to_state: nonexistent_state}
compensation: {action: DEMO.ACT.WITHDRAW, policy: manual}
"""

SCN_COMPENSATE = """
scenario_id: DEMO.SCN.COMPENSATE
title: 补偿路径
steps:
  - {id: bad, action: DEMO.ACT.GUARDFAIL}
"""

# resume 用:to_state 先坏(v1)后修(v2)
ACT_RESUME = """
action_id: DEMO.ACT.RESUME
title: 断点续跑
target: {dataset: demo_ms3_alerts, object_class: 告警事件}
effect: {type: update_lifecycle, to_state: %s}
idempotency_key: "{{ target.object_id }}"
"""

SCN_RESUME = """
scenario_id: DEMO.SCN.RESUME
title: 续跑场景
steps:
  - {id: step1, action: DEMO.ACT.RESUME}
"""


class _PassthroughChecker:
    def get_acl(self, dataset, role):
        return None

    def _get_denies(self, dotted):
        return []

    def check_dataset_access(self, *, role, dataset, action, permissions=None):
        return True

    def apply_table_filter(self, table, dataset, role):
        return table


@pytest.fixture
def world(tmp_path):
    base = str(tmp_path / "data")
    cfg = ArrowLakeConfig()
    cfg.storage = StorageConfig(base_uri=base, backend=StorageBackend.LOCAL)
    lake = Lake(base_uri=base, config=cfg)
    schema = pa.schema([
        ("alert_id", pa.string()), ("pressure", pa.float64()),
        ("level", pa.string()), ("state", pa.string()),
        ("published_at", pa.string()),
    ])
    lake.create_dataset(
        demo.DEMO_DATASET, pa.Table.from_pylist(demo.ALERT_ROWS, schema=schema))

    db = SystemDB(":memory:")
    Migrator(db).run()
    ContractStore(db).save_contract(demo.DEMO_DATASET, demo.CONTRACT_YAML)
    rules = OntologyRulesStore(db)
    for r in demo.RULES:
        rules.upsert_rule(**r)
        rules.transition(r["rule_id"], "active")
    catalog = ActionCatalogStore(db)
    for y in (demo.ACTION_PUBLISH, demo.ACTION_ESCALATE, demo.ACTION_NOTIFY):
        catalog.save_action(y.splitlines()[1].split(":", 1)[1].strip(), y)
    ScenarioStore(db).save_scenario("GAS.LEAK.RESPONSE", demo.SCENARIO_YAML)
    uid = IdentityStore(db).create_user("op", role="editor")
    yield SimpleNamespace(lake=lake, db=db, uid=uid)
    db.close()


def _client(world, *, role: Role, user_id: int | None = None) -> TestClient:
    from arrow_lake.api.routers.actions import router as actions_router

    app = FastAPI()
    app.state.lake = world.lake
    app.state.checker = _PassthroughChecker()
    app.state.contract_store = ContractStore(world.db)
    app.state.semantic_alignment_store = SemanticAlignmentStore(world.db)
    app.state.ontology_rules_store = OntologyRulesStore(world.db)
    app.state.action_store = ActionCatalogStore(world.db)
    app.state.idempotency_store = IdempotencyStore(world.db)
    app.state.scenario_store = ScenarioStore(world.db)
    app.state.scenario_instance_store = ScenarioInstanceStore(world.db)
    app.state.user_state_store = UserStateStore(world.db)

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(
            sub="op", role=role, permissions=[],
            user_id=user_id if user_id is not None else world.uid, exp=0, iat=0,
        )
        return await call_next(request)

    app.include_router(actions_router)
    return TestClient(app)


def _instantiate(client: TestClient, scenario_id: str, object_id: str):
    return client.post(
        f"/api/v1/actions/scenarios/{scenario_id}/instantiate",
        json={
            "dataset": demo.DEMO_DATASET,
            "object_type": "alerts",
            "object_id": object_id,
            "reason": "e2e 测试",
        },
    )


def _await_terminal(client: TestClient, iid: int, timeout: float = 15.0) -> dict:
    """轮询实例详情直到终态(runner 在 TestClient portal loop 上后台跑)。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/v1/actions/scenarios/instances/{iid}")
        assert r.status_code == 200, r.text
        inst = r.json()["instance"]
        if inst["status"] != "running":
            return r.json()
        time.sleep(0.05)
    raise AssertionError(f"instance {iid} still running after {timeout}s")


# --- instantiate:鉴权与校验 ---------------------------------------------------


def test_viewer_cannot_instantiate(world) -> None:
    with _client(world, role=Role.VIEWER) as c:
        r = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D001")
        assert r.status_code == 403
        # 列表/详情 VIEWER 可读
        assert c.get("/api/v1/actions/scenarios/instances").status_code == 200
        assert c.get("/api/v1/actions/scenarios/instances/1").status_code == 404


def test_instantiate_unknown_scenario_404(world) -> None:
    with _client(world, role=Role.EDITOR) as c:
        r = _instantiate(c, "NO.SUCH.SCENARIO", "GAS.ALERT.D001")
        assert r.status_code == 404


def test_instantiate_missing_object_404(world) -> None:
    with _client(world, role=Role.EDITOR) as c:
        r = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.GHOST")
        assert r.status_code == 404


# --- XOR 双臂经真中间件 -------------------------------------------------------


def test_high_pressure_runs_publish_and_notify(world) -> None:
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        r = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D001")
        assert r.status_code == 202, r.text
        iid = r.json()["instance_id"]

        detail = _await_terminal(c, iid)
        inst = detail["instance"]
        assert inst["status"] == "completed", inst.get("error")
        runs = {s["step_id"]: s["status"] for s in detail["step_runs"]}
        # D001:matched=3(OPEN+HIGH+LEVEL)≥2 → then 臂 publish;escalate 落选;
        # notify_ops 依赖 publish 串行后跑
        assert runs == {
            "assess": "succeeded",
            "publish": "succeeded",
            "notify_ops": "succeeded",
            "escalate_manual": "skipped",
        }
        # update_lifecycle 真写
        rows = world.lake.olap_query(
            demo.DEMO_DATASET,
            f'SELECT state, published_at FROM "{demo.DEMO_DATASET}" '
            f"WHERE alert_id = 'GAS.ALERT.D001'",
        ).table.to_pylist()
        assert rows[0]["state"] == "published"
        assert rows[0]["published_at"] is not None
        # 上下文含步输出
        assert detail["instance"]["context"]["steps"]["assess"]["matched_rules"] == 3


def test_low_pressure_runs_escalate_arm(world) -> None:
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        r = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D002")
        assert r.status_code == 202
        detail = _await_terminal(c, r.json()["instance_id"])
        assert detail["instance"]["status"] == "completed"
        runs = {s["step_id"]: s["status"] for s in detail["step_runs"]}
        # D002:matched=1(OPEN)<2 → else 臂 escalate;publish skipped 且
        # notify_ops 级联 skipped
        assert runs == {
            "assess": "succeeded",
            "escalate_manual": "succeeded",
            "publish": "skipped",
            "notify_ops": "skipped",
        }
        rows = world.lake.olap_query(
            demo.DEMO_DATASET,
            f"SELECT state FROM \"{demo.DEMO_DATASET}\" WHERE alert_id = 'GAS.ALERT.D002'",
        ).table.to_pylist()
        assert rows[0]["state"] == "escalated"


def test_entries_not_matched_422(world) -> None:
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        # 先跑一次把 D001 翻 published
        r = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D001")
        assert r.status_code == 202
        _await_terminal(c, r.json()["instance_id"])
        # 再实例化:entries 要求 state==pending → 422
        r2 = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D001")
        assert r2.status_code == 422
        assert "entry" in r2.json()["detail"]


# --- 列表 / 详情 ---------------------------------------------------------------


def test_list_instances_filter_and_detail(world) -> None:
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        a = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D001").json()["instance_id"]
        _await_terminal(c, a)
        b = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D002").json()["instance_id"]
        _await_terminal(c, b)

        r = c.get("/api/v1/actions/scenarios/instances?scenario_id=GAS.LEAK.RESPONSE")
        assert r.status_code == 200
        assert r.json()["total"] == 2
        r2 = c.get(
            "/api/v1/actions/scenarios/instances"
            "?scenario_id=GAS.LEAK.RESPONSE&status=completed"
        )
        assert r2.json()["total"] == 2
        r3 = c.get("/api/v1/actions/scenarios/instances?status=running")
        assert r3.json()["total"] == 0
        # 详情字段齐(解码后的 pending_compensation 列表)
        d = c.get(f"/api/v1/actions/scenarios/instances/{a}").json()
        assert d["instance"]["pending_compensation"] == []
        assert d["instance"]["scenario_id"] == "GAS.LEAK.RESPONSE"
        assert d["step_runs"], "step_runs should be recorded"


def test_detail_404(world) -> None:
    with _client(world, role=Role.EDITOR) as c:
        assert c.get("/api/v1/actions/scenarios/instances/999").status_code == 404


# --- terminate / resume -------------------------------------------------------


def test_terminate_requires_admin_and_running(world) -> None:
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        a = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D001").json()["instance_id"]
        _await_terminal(c, a)
        # 非 ADMIN → 403
        r = c.post(f"/api/v1/actions/scenarios/instances/{a}/terminate")
        assert r.status_code == 403
    with _client(world, role=Role.ADMIN) as c:
        # completed → 409
        r = c.post(f"/api/v1/actions/scenarios/instances/{a}/terminate")
        assert r.status_code == 409


def test_resume_after_catalog_fix_completes(world) -> None:
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        ActionCatalogStore(world.db).save_action("DEMO.ACT.RESUME", ACT_RESUME % "nonexistent")
        ScenarioStore(world.db).save_scenario("DEMO.SCN.RESUME", SCN_RESUME)
        r = _instantiate(c, "DEMO.SCN.RESUME", "GAS.ALERT.D001")
        assert r.status_code == 202
        iid = r.json()["instance_id"]
        detail = _await_terminal(c, iid)
        assert detail["instance"]["status"] == "failed"  # 词表守卫 422 → 步 failed

        # running 实例不可 resume(409)——本例已 failed;修复目录 v2 后续跑
        r_bad = c.post(f"/api/v1/actions/scenarios/instances/{iid}/resume")
        assert r_bad.status_code == 200, r_bad.text
        detail2 = _await_terminal(c, iid)
        # 未修目录,续跑仍失败(REJECT 步重试)
        assert detail2["instance"]["status"] == "failed"

        ActionCatalogStore(world.db).save_action("DEMO.ACT.RESUME", ACT_RESUME % "escalated")
        r3 = c.post(f"/api/v1/actions/scenarios/instances/{iid}/resume")
        assert r3.status_code == 200
        detail3 = _await_terminal(c, iid)
        assert detail3["instance"]["status"] == "completed"
        runs = {s["step_id"]: s["status"] for s in detail3["step_runs"]}
        assert runs == {"step1": "succeeded"}
        rows = world.lake.olap_query(
            demo.DEMO_DATASET,
            f"SELECT state FROM \"{demo.DEMO_DATASET}\" WHERE alert_id = 'GAS.ALERT.D001'",
        ).table.to_pylist()
        assert rows[0]["state"] == "escalated"

        # completed 不可再 resume(409)
        r4 = c.post(f"/api/v1/actions/scenarios/instances/{iid}/resume")
        assert r4.status_code == 409


# --- 补偿端到端 -----------------------------------------------------------------


def test_guard_failure_with_compensation_marks_pending(world) -> None:
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        ActionCatalogStore(world.db).save_action("DEMO.ACT.GUARDFAIL", ACT_GUARD_FAIL)
        ScenarioStore(world.db).save_scenario("DEMO.SCN.COMPENSATE", SCN_COMPENSATE)
        r = _instantiate(c, "DEMO.SCN.COMPENSATE", "GAS.ALERT.D002")
        assert r.status_code == 202
        iid = r.json()["instance_id"]
        detail = _await_terminal(c, iid)

        inst = detail["instance"]
        assert inst["status"] == "compensated"
        assert inst["pending_compensation"] == ["DEMO.ACT.WITHDRAW"]
        bad = next(s for s in detail["step_runs"] if s["step_id"] == "bad")
        assert bad["status"] == "failed"
        assert bad["output"]["pending_compensation"] == ["DEMO.ACT.WITHDRAW"]
        # 补偿待办走既有单 action execute 端点人工执行(console 同路)
        withdraw_yaml = ACT_GUARD_FAIL.replace(
            "DEMO.ACT.GUARDFAIL", "DEMO.ACT.WITHDRAW"
        ).replace("to_state: nonexistent_state", "to_state: pending").replace(
            "\ncompensation: {action: DEMO.ACT.WITHDRAW, policy: manual}", ""
        )
        ActionCatalogStore(world.db).save_action("DEMO.ACT.WITHDRAW", withdraw_yaml)
        w = c.post(
            "/api/v1/actions/DEMO.ACT.WITHDRAW/execute",
            json={
                "dataset": demo.DEMO_DATASET,
                "object_type": "alerts",
                "object_id": "GAS.ALERT.D002",
                "reason": "人工补偿核销",
                "scenario_id": "DEMO.SCN.COMPENSATE",
                "step_id": "bad",
            },
        )
        assert w.status_code == 200, w.text
        assert w.json()["status"] == "executed"
