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


def _client(
    world, *, role: Role, user_id: int | None = None, checker: object | None = None
) -> TestClient:
    from arrow_lake.api.routers.actions import router as actions_router

    app = FastAPI()
    app.state.lake = world.lake
    app.state.checker = checker or _PassthroughChecker()
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


# --- H-3②:实例读列级 ACL(v1.11.6.6)---------------------------------------


class _ColAclChecker(_PassthroughChecker):
    """读面复查用:visible 列集 / 读权开关(执行面沿用 passthrough 语义)。"""

    def __init__(self, visible: list[str] | None = None, read_ok: bool = True) -> None:
        self.visible = visible
        self.read_ok = read_ok

    def get_acl(self, dataset, role):
        if self.visible is None:
            return None
        from arrow_lake.api.rbac import DatasetACL

        return DatasetACL(
            dataset=dataset, role="editor", visible_columns=self.visible,
            row_filter=None, denied_actions=[],
        )

    def check_dataset_access(self, *, role, dataset, action, permissions=None):
        return self.read_ok


def test_instance_detail_prunes_target_columns(world) -> None:
    """H-3②:列受限读者看实例 detail——context.target 裁到可见列集 +
    acl_pruned 标记;无读权 → target 置空;原始 context_json 不回。
    (实例化者视角不得成为越权查看通道。)"""
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        a = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D001").json()["instance_id"]
        detail = _await_terminal(c, a)
        assert detail["instance"]["status"] == "completed"
        target_full = detail["instance"]["context"]["target"]
        assert "pressure" in target_full  # 实例化者视角完整
        assert "context_json" not in detail["instance"]  # 原始串不旁路

    with _client(
        world, role=Role.EDITOR, user_id=world.uid,
        checker=_ColAclChecker(visible=["alert_id", "state"]),
    ) as c:
        d = c.get(f"/api/v1/actions/scenarios/instances/{a}").json()
        ctx = d["instance"]["context"]
        assert set(ctx["target"]) == {"alert_id", "state"}  # 隐藏列裁掉
        assert ctx["acl_pruned"] is True
        assert "context_json" not in d["instance"]
    # 无读权 → target 置空+标记
    with _client(
        world, role=Role.VIEWER, user_id=world.uid,
        checker=_ColAclChecker(read_ok=False),
    ) as c:
        d2 = c.get(f"/api/v1/actions/scenarios/instances/{a}").json()
        assert d2["instance"]["context"]["target"] == {}
        assert d2["instance"]["context"]["acl_pruned"] is True


def test_instance_list_strips_context_json(world) -> None:
    """H-3②:列表行不回 context_json(完整 target 走详情端点的裁剪路径);
    M12:offset 翻页 + total 为过滤后真总数。"""
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        a = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D001").json()["instance_id"]
        _await_terminal(c, a)
        body = c.get("/api/v1/actions/scenarios/instances").json()
        assert body["instances"]
        assert all("context_json" not in r for r in body["instances"])
        assert body["total"] == len(body["instances"])  # 单页场景下也一致
        # 第二实例 + 过滤翻页语义
        b = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D002").json()["instance_id"]
        _await_terminal(c, b)
        page = c.get("/api/v1/actions/scenarios/instances?limit=1&offset=1").json()
        assert page["total"] == 2 and len(page["instances"]) == 1  # total≠当前页行数
        assert page["instances"][0]["id"] == a  # id DESC,第二页=较旧实例


# --- M8/M9(v1.11.6.6)-------------------------------------------------------


def test_instantiate_rejects_duplicate_running(world) -> None:
    """M9:同 (scenario,dataset,object) 已有 running 实例 → 409(防双活)。"""
    from arrow_lake.system_db.stores.scenario_instances import ScenarioInstanceStore

    ScenarioInstanceStore(world.db).create_instance(
        scenario_id="GAS.LEAK.RESPONSE", scenario_version=1,
        dataset=demo.DEMO_DATASET, object_type="alerts",
        object_id="GAS.ALERT.D001", actor="op",
    )
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        r = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D001")
        assert r.status_code == 409
        assert "already running" in r.json()["detail"]
        # 不同对象不受影响
        r2 = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D002")
        assert r2.status_code == 202


def test_compensation_ack_clears_pending(world) -> None:
    """M8:核销端点清该步声明的待办;重复核销/无声明步 409。"""
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        ActionCatalogStore(world.db).save_action("DEMO.ACT.GUARDFAIL", ACT_GUARD_FAIL)
        ScenarioStore(world.db).save_scenario("DEMO.SCN.COMPENSATE", SCN_COMPENSATE)
        iid = _instantiate(c, "DEMO.SCN.COMPENSATE", "GAS.ALERT.D002").json()["instance_id"]
        detail = _await_terminal(c, iid)
        assert detail["instance"]["status"] == "compensated"
        assert detail["instance"]["pending_compensation"] == ["DEMO.ACT.WITHDRAW"]

        r = c.post(f"/api/v1/actions/scenarios/instances/{iid}/compensation/bad/ack")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["acked"] == ["DEMO.ACT.WITHDRAW"]
        assert body["pending_compensation"] == []  # 待办清空
        d2 = c.get(f"/api/v1/actions/scenarios/instances/{iid}").json()
        assert d2["instance"]["pending_compensation"] == []
        # 重复核销 → 409(无剩余待办)
        assert c.post(
            f"/api/v1/actions/scenarios/instances/{iid}/compensation/bad/ack"
        ).status_code == 409
        # 无补偿声明的步 → 409
        assert c.post(
            f"/api/v1/actions/scenarios/instances/{iid}/compensation/assess/ack"
        ).status_code == 409


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


def test_resume_pins_instance_scenario_version(world) -> None:
    """H-2(v1.11.6.6):实例锚 v1;场景升 v2(步集改名)后 resume——
    runner 仍按 v1 的 step 集续跑(不修则 resume 取最新版 + middleware
    归属校验按 v2 把 step1 误拒 422,实例必 failed)。"""
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        catalog = ActionCatalogStore(world.db)
        store = ScenarioStore(world.db)
        catalog.save_action("DEMO.ACT.RESUME", ACT_RESUME % "nonexistent")
        scn = SCN_RESUME.replace("DEMO.SCN.RESUME", "DEMO.SCN.PIN")
        store.save_scenario("DEMO.SCN.PIN", scn)  # v1:step1
        iid = _instantiate(c, "DEMO.SCN.PIN", "GAS.ALERT.D001").json()["instance_id"]
        detail = _await_terminal(c, iid)
        assert detail["instance"]["status"] == "failed"  # 词表守卫
        v1 = store.get_version("DEMO.SCN.PIN")["version"]

        # 升 v2:步 id 换名(全新步集)
        store.save_scenario("DEMO.SCN.PIN", scn.replace("step1", "step_new"))
        assert store.get_version("DEMO.SCN.PIN")["version"] == v1 + 1

        # 修 action 词表后 resume:续跑按 v1 的 step1;v2 的 step_new 不混入
        catalog.save_action("DEMO.ACT.RESUME", ACT_RESUME % "escalated")
        r = c.post(f"/api/v1/actions/scenarios/instances/{iid}/resume")
        assert r.status_code == 200, r.text
        detail2 = _await_terminal(c, iid)
        assert detail2["instance"]["status"] == "completed", detail2["instance"].get("error")
        runs = {s["step_id"]: s["status"] for s in detail2["step_runs"]}
        assert runs == {"step1": "succeeded"}


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


# --- v1.11.6.6 收敛批:审查加固(安全 H-1/质量 H-1/安全 M-1)--------------------


def test_instance_detail_skeletonizes_steps_for_restricted_reader(world) -> None:
    """安全 H-1(收敛):target 受限(列裁/无读权)时,context.steps 与
    step_runs 的 output/error 同步骨架化——幂等键模板可嵌隐藏列明文、
    错误串可嵌单元格值,无法按列裁只能整体剥(时间线 status 保留)。"""
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        a = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D001").json()["instance_id"]
        detail = _await_terminal(c, a)
        assert detail["instance"]["status"] == "completed"
        assert detail["instance"]["context"]["steps"]  # 实例化者视角完整
        assert any(r["output"] for r in detail["step_runs"])

    with _client(
        world, role=Role.EDITOR, user_id=world.uid,
        checker=_ColAclChecker(visible=["alert_id", "state"]),
    ) as c:
        d = c.get(f"/api/v1/actions/scenarios/instances/{a}").json()
        assert d["instance"]["context"]["steps"] == {}  # steps 整体剥
        for r in d["step_runs"]:
            assert r["output"] == {"acl_pruned": True}
            assert r["error"] is None
            assert r["status"] in ("succeeded", "skipped")  # 时间线骨架保留
    # 无读权:target/steps 双清
    with _client(
        world, role=Role.VIEWER, user_id=world.uid,
        checker=_ColAclChecker(read_ok=False),
    ) as c:
        d2 = c.get(f"/api/v1/actions/scenarios/instances/{a}").json()
        assert d2["instance"]["context"]["steps"] == {}
        assert all(r["output"] == {"acl_pruned": True} for r in d2["step_runs"])


def test_pruned_target_ctx_container_dataset_no_object_type(monkeypatch) -> None:
    """质量 H-1(收敛):object_type 不得作为表名传列 ACL 查找——容器表
    dataset("gas.segments")会被拼成三段 miss → fail-open 零裁剪。"""
    import arrow_lake.api.routers.actions as act_mod

    calls: dict = {}

    def fake_cvc(request, dataset, table=None):
        calls["dataset"], calls["table"] = dataset, table
        return frozenset({"uid"})  # 命中 gas.segments 键 → 列受限

    monkeypatch.setattr(act_mod, "get_checker", lambda req: _PassthroughChecker())
    monkeypatch.setattr("arrow_lake.api.deps.caller_visible_columns", fake_cvc)
    monkeypatch.setattr(
        "arrow_lake.api.deps._deny_table_override",
        lambda req, key, write=False: False,
    )
    user = SimpleNamespace(role=Role.EDITOR, permissions=None)
    rec = {"dataset": "gas.segments", "object_type": "alerts"}
    ctx = {"target": {"uid": "u1", "secret_col": "s"}, "steps": {"s1": {"o": 1}}}
    out, restricted = act_mod._pruned_target_ctx(None, user, rec, ctx)
    assert calls["table"] is None  # 关键:不再把 object_type 当表名
    assert calls["dataset"] == "gas.segments"
    assert out["target"] == {"uid": "u1"} and restricted is True


def test_pruned_target_ctx_table_deny_clears_target(monkeypatch) -> None:
    """安全 M-1(收敛):dataset 自身作为二段键的表级 deny → target 清空
    (check_dataset_access 的 dataset 键查找盖不到 ds.table deny)。"""
    import arrow_lake.api.routers.actions as act_mod

    monkeypatch.setattr(act_mod, "get_checker", lambda req: _PassthroughChecker())
    monkeypatch.setattr(
        "arrow_lake.api.deps.caller_visible_columns",
        lambda req, dataset, table=None: None,
    )
    seen: list[str] = []

    def fake_deny(req, key, write=False):
        seen.append(key)
        return key == "gas.segments" and not write

    monkeypatch.setattr("arrow_lake.api.deps._deny_table_override", fake_deny)
    user = SimpleNamespace(role=Role.EDITOR, permissions=None)
    rec = {"dataset": "gas.segments", "object_type": "alerts"}
    ctx = {"target": {"uid": "u1"}}
    out, restricted = act_mod._pruned_target_ctx(None, user, rec, ctx)
    assert out["target"] == {} and out["acl_pruned"] is True and restricted is True
    assert "gas.segments" in seen


def test_instance_list_filters_rows_by_dataset_read(world) -> None:
    """安全 M-1(收敛):非 ADMIN 列表行按 dataset 读权过滤——整库拒读
    用户不得经实例列表枚举对象标识;ADMIN 不滤。"""
    with _client(world, role=Role.EDITOR, user_id=world.uid) as c:
        a = _instantiate(c, "GAS.LEAK.RESPONSE", "GAS.ALERT.D001").json()["instance_id"]
        _await_terminal(c, a)
    with _client(
        world, role=Role.VIEWER, user_id=world.uid,
        checker=_ColAclChecker(read_ok=False),
    ) as c:
        body = c.get("/api/v1/actions/scenarios/instances").json()
        assert body["instances"] == []  # 无读权 → 行全滤
    with _client(world, role=Role.ADMIN, user_id=world.uid) as c:
        body = c.get("/api/v1/actions/scenarios/instances").json()
        assert any(r["id"] == a for r in body["instances"])  # ADMIN 不滤
