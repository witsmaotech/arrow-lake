"""W5.2 — MS3 DoD e2e:vertical slice 告警→研判→发布→幂等→越权(F3.5)。

**DoD 断言**(实施计划 §3 W5.2):assess → xor gate(场景规范由代码管线
执行,S3)→ publish(**lifecycle 真写迁移 + 审计归属 scenario/step/rule_ids
+ post_event + 通知落库**)→ 幂等重放(200 已生效不重复写)→ 越权 403;
escalate 替代路径;unruly 隔离(S8)。

真链路:真 Lake(LOCAL 后端 hermetic)+ 真存储 sys_audit_trail 回查 +
真 user_state 通知 + 真谓词/规则。资产=testing/ms3_demo.py(D4 单表演示
集;演示数据非业务契约内容,v1.11.0.1 W5.2 先例)。
"""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest
from arrow_lake import Lake
from arrow_lake.actions import events
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.config import ArrowLakeConfig, StorageBackend, StorageConfig
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.actions import ActionCatalogStore
from arrow_lake.system_db.stores.contracts import ContractStore
from arrow_lake.system_db.stores.identity import IdentityStore
from arrow_lake.system_db.stores.ontology import OntologyRulesStore
from arrow_lake.system_db.stores.scenarios import ScenarioStore
from arrow_lake.system_db.stores.semantic_alignments import SemanticAlignmentStore
from arrow_lake.system_db.stores.user_state import UserStateStore
from arrow_lake.testing import ms3_demo as demo
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


class _PassthroughChecker:
    """直通 checker(无 ACL 行);安全面由接线审计测试负责。"""

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
    """共享世界:真 Lake(本地 lance)+ 真 stores;app 工厂按角色建客户端。"""
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

    events.reset_subscribers()
    yield SimpleNamespace(
        lake=lake, db=db, uid=uid,
        notify_store=UserStateStore(db),
        scenario_yaml=demo.SCENARIO_YAML,
    )
    events.reset_subscribers()
    db.close()


def _client(world, *, role: Role, permissions: list[str] | None = None,
            user_id: int | None = None) -> TestClient:
    from arrow_lake.api.routers.actions import router as actions_router
    from arrow_lake.api.routers.decisions import router as decisions_router
    from arrow_lake.api.routers.objects import router as objects_router

    app = FastAPI()
    app.state.lake = world.lake
    app.state.checker = _PassthroughChecker()
    app.state.contract_store = ContractStore(world.db)
    app.state.semantic_alignment_store = SemanticAlignmentStore(world.db)
    app.state.entity_map_store = None
    app.state.ontology_rules_store = OntologyRulesStore(world.db)
    from arrow_lake.system_db.stores.actions import IdempotencyStore

    app.state.action_store = ActionCatalogStore(world.db)
    app.state.idempotency_store = IdempotencyStore(world.db)
    app.state.scenario_store = ScenarioStore(world.db)
    app.state.user_state_store = UserStateStore(world.db)

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(
            sub="op", role=role, permissions=permissions or [],
            user_id=user_id, exp=0, iat=0,
        )
        return await call_next(request)

    app.include_router(objects_router)
    app.include_router(decisions_router)
    app.include_router(actions_router)
    return TestClient(app)


def _editor(world, permissions=None):
    return _client(world, role=Role.EDITOR, permissions=permissions,
                   user_id=world.uid)


def _assess(c: TestClient, object_id: str) -> dict:
    r = c.post("/api/v1/decisions/assess", json={
        "dataset": demo.DEMO_DATASET, "object_type": "alerts",
        "object_id": object_id,
    })
    assert r.status_code == 200, r.text
    return r.json()


def _execute(c: TestClient, action_id: str, object_id: str, *,
             reason: str, scenario_id=None, step_id=None, assess_ctx=None):
    return c.post(f"/api/v1/actions/{action_id}/execute", json={
        "dataset": demo.DEMO_DATASET, "object_type": "alerts",
        "object_id": object_id, "reason": reason,
        "scenario_id": scenario_id, "step_id": step_id,
        "assess": assess_ctx,
    })


def _payload(entry) -> dict:
    """audit_query 条目的 payload 可能是 dict 或 pair-list 序列化形态。"""
    p = getattr(entry, "payload", entry)
    if isinstance(p, dict):
        return p
    try:
        return dict(p)
    except Exception:  # pragma: no cover
        return {}


def _object_state(c: TestClient, object_id: str) -> dict:
    r = c.post("/api/v1/objects/query", json={
        "dataset": demo.DEMO_DATASET, "object_type": "alerts",
        "filter": [{"column": "alert_id", "op": "eq", "value": object_id}],
    })
    assert r.status_code == 200, r.text
    return r.json()["objects"][0]


class TestVerticalSliceDoD:
    def test_publish_path_full_chain(self, world) -> None:
        """主路径:研判→gate→发布(真写+审计归属+事件+通知)→幂等重放。"""
        editor = _editor(world)
        got: list[events.ActionEvent] = []
        unsub = events.subscribe("alert.published", got.append)
        try:
            # ① 研判(D001:高压高风险,命中 3 条)
            a = _assess(editor, "GAS.ALERT.D001")
            assert {c["rule_id"] for c in a["conclusions"]} == \
                demo.GOLDEN_EXPECTED["GAS.ALERT.D001"]
            assert a["unruly"] == ["DEMO.R.UNRULY"]      # S8:坏规则隔离
            assert a["confidence"] == 1.0
            assert {"GAS.ALERT.PUBLISH", "GAS.ALERT.NOTIFY",
                    "GAS.ALERT.ESCALATE"} <= set(a["actionable"])

            # ② xor gate(场景规范,代码管线执行,S3):matched>=2 → publish
            assert a["matched_rules"] >= 2
            assess_ctx = {
                "confidence": a["confidence"],
                "matched_rules": a["matched_rules"],
                "rule_ids": [c["rule_id"] for c in a["conclusions"]],
            }

            # ③ 执行 publish(scenario/step 归属)
            r = _execute(editor, "GAS.ALERT.PUBLISH", "GAS.ALERT.D001",
                         reason="研判命中高压泄漏,按场景发布",
                         scenario_id="GAS.LEAK.RESPONSE", step_id="publish",
                         assess_ctx=assess_ctx)
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "executed"

            # ④ lifecycle 真写迁移(update_lifecycle,真 lance 行级 update)
            obj = _object_state(editor, "GAS.ALERT.D001")
            assert obj["lifecycle_state"] == "published"
            assert obj["attributes"]["published_at"]  # {{ now() }} 已渲染

            # ⑤ 审计归属:真 sys_audit_trail 回查,含 scenario/step/依据
            entries = world.lake.audit_query(event_type="action.execute")
            hit = [e for e in entries
                   if _payload(e).get("object_id") == "GAS.ALERT.D001"]
            assert hit, "action.execute audit entry missing"
            p = _payload(hit[-1])
            assert p["scenario_id"] == "GAS.LEAK.RESPONSE"
            assert p["step_id"] == "publish"
            assert "DEMO.R.HIGH" in p["included"]["assess.rule_ids"]

            # ⑥ post_event(F3.4):订阅者收到结构化 payload
            assert len(got) == 1
            assert got[0].payload["target.object_id"] == "GAS.ALERT.D001"
            assert "DEMO.R.HIGH" in got[0].payload["assess.rule_ids"]
            assert got[0].payload["actor"]["sub"] == "op"

            # ⑦ and_split 第二支:notify_ops → 通知落库(user_state)
            r3 = _execute(editor, "GAS.ALERT.NOTIFY", "GAS.ALERT.D001",
                          reason="场景并行通知",
                          scenario_id="GAS.LEAK.RESPONSE", step_id="notify_ops",
                          assess_ctx=assess_ctx)
            assert r3.status_code == 200
            notes = world.notify_store.list_notifications(world.uid)
            assert any("GAS.ALERT.D001" in n["message"] and n["kind"] == "action"
                       for n in notes)
            # 重放前基线(publish+notify 各有一条执行审计)
            audits_before = len(
                world.lake.audit_query(event_type="action.execute"))
            notes_before = len(notes)

            # ⑧ 幂等重放:200 已生效,不重复写/审计/通知
            r4 = _execute(editor, "GAS.ALERT.PUBLISH", "GAS.ALERT.D001",
                          reason="重放验证",
                          scenario_id="GAS.LEAK.RESPONSE", step_id="publish",
                          assess_ctx=assess_ctx)
            assert r4.status_code == 200
            assert r4.json()["status"] == "already_in_effect"
            assert _object_state(editor, "GAS.ALERT.D001")["lifecycle_state"] \
                == "published"
            assert len(world.lake.audit_query(event_type="action.execute")) \
                == audits_before
            assert len(world.notify_store.list_notifications(world.uid)) \
                == notes_before
        finally:
            unsub()

    def test_escalate_substitute_path(self, world) -> None:
        """替代路径:D002 命中不足 → gate else 分支 → escalate_manual。"""
        editor = _editor(world)
        a = _assess(editor, "GAS.ALERT.D002")
        assert a["matched_rules"] == 1  # gate(>=2)不满足 → else 分支
        r = _execute(editor, "GAS.ALERT.ESCALATE", "GAS.ALERT.D002",
                     reason="研判置信不足,升级人工",
                     scenario_id="GAS.LEAK.RESPONSE", step_id="escalate_manual",
                     assess_ctx={"rule_ids": [c["rule_id"] for c in a["conclusions"]]})
        assert r.status_code == 200 and r.json()["status"] == "executed"
        assert _object_state(editor, "GAS.ALERT.D002")["lifecycle_state"] \
            == "escalated"

    def test_viewer_cannot_execute_403(self, world) -> None:
        viewer = _client(world, role=Role.VIEWER, user_id=world.uid)
        r = _execute(viewer, "GAS.ALERT.PUBLISH", "GAS.ALERT.D001",
                     reason="越权尝试")
        assert r.status_code == 403

    def test_scoped_token_denial_audited(self, world) -> None:
        """D3:scoped token 缺权限 → 403 + action.denied 审计(度量数据面)。"""
        ActionCatalogStore(world.db).save_action("DEMO.SCOPED", """
action_id: DEMO.SCOPED
title: 受权限保护
target: {dataset: demo_ms3_alerts, object_class: 告警事件}
permission: alerts:publish
effect: {type: none}
""")
        editor = _editor(world, permissions=["other:scope"])
        r = _execute(editor, "DEMO.SCOPED", "GAS.ALERT.D001", reason="x")
        assert r.status_code == 403
        denied = world.lake.audit_query(event_type="action.denied")
        assert any(_payload(e).get("action_id") == "DEMO.SCOPED" for e in denied)
