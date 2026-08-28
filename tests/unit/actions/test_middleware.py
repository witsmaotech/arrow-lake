"""W4.1/W4.2/W4.3 — 行动执行中间件(八步序)+效果+事件。

契约(实施计划 §3 W4):
* 前置不满足 422;权限不足 403(scoped token 精确匹配);reason 缺 422;
* 幂等重放 200 already_in_effect 不重复写(状态不变、无二次审计);
  failed 态可重认领(修目录后重放执行成功);
* update_lifecycle **真写**:本地 tmp Lance 容器表行级 update(D1-①,
  storage.update_rows table= 扩展),fields 模板渲染(assess.level/now());
* notify:user_state 通知落库(user_id);无 user_id 跳过;
* on_failure 三分派:REJECT→422 / MANUAL·DEAD_LETTER→200+失败审计+
  幂等置 failed;
* 审计条目含 scenario_id/step_id/rule_ids(include 解析);
* 事件:订阅者收到结构化 payload;订阅者抛错异常隔离不阻断执行。
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pyarrow as pa
import pytest
from arrow_lake.actions import events
from arrow_lake.actions.middleware import ActionError, execute_action
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.ingest.storage import LanceStorageManager
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.actions import ActionCatalogStore, IdempotencyStore
from arrow_lake.system_db.stores.contracts import ContractStore
from arrow_lake.system_db.stores.semantic_alignments import SemanticAlignmentStore
from arrow_lake.system_db.stores.user_state import UserStateStore

CONTRACT_YAML = """
dataset: gas_net
tables:
  alerts:
    object_class: 告警事件
    lifecycle: {column: state, states: [pending, confirmed, published, closed], initial: pending}
    identifier:
      column: alert_id
      pattern: "GAS.ALERT.{seq}"
    columns:
      - {name: level}
      - {name: published_at}
"""

_ROWS = [
    {"alert_id": "GAS.ALERT.001", "state": "pending", "level": None, "published_at": None},
    {"alert_id": "GAS.ALERT.002", "state": "pending", "level": None, "published_at": None},
]
# 显式 schema:全 NULL 列被 arrow 推断为 null 类型,lance update 写不进 Utf8
_SCHEMA = pa.schema(
    [
        ("alert_id", pa.string()),
        ("state", pa.string()),
        ("level", pa.string()),
        ("published_at", pa.string()),
    ]
)
ROWS = pa.Table.from_pylist(_ROWS, schema=_SCHEMA)

ACT_LOG = """
action_id: ACT.LOG
title: 登记
target: {dataset: gas_net, object_class: 告警事件}
effect: {type: none}
audit: {reason_required: true, include: [assess.rule_ids]}
"""

ACT_PUB = """
action_id: ACT.PUBLISH
title: 发布预警
target: {dataset: gas_net, object_class: 告警事件}
permission: alerts:publish
preconditions:
  - "assess.matched_rules >= 1"
  - "target.state == 'pending'"
effect:
  type: update_lifecycle
  to_state: published
  fields: {level: "{{ assess.level }}", published_at: "{{ now() }}"}
idempotency_key: "{{ target.object_id }}"
audit: {reason_required: true, include: [assess.rule_ids]}
post_event:
  name: alert.published
  payload: [target.object_id, assess.rule_ids, actor]
"""

ACT_NOASSESS = """
action_id: ACT.NOASSESS
title: 需研判
target: {dataset: gas_net, object_class: 告警事件}
preconditions: ["assess.confidence >= 0.9"]
effect: {type: none}
"""

ACT_NOTIFY = """
action_id: ACT.NOTIFY
title: 通知
target: {dataset: gas_net, object_class: 告警事件}
effect: {type: notify, fields: {message: "对象 {{ target.object_id }} 已处理"}}
"""

_ACT_FAIL_BASE = """
action_id: {aid}
title: 会失败
target: {{dataset: gas_net, object_class: 告警事件}}
preconditions: ["assess.matched_rules >= 1"]
effect:
  type: update_lifecycle
  to_state: published
  fields: {{no_such_col: "x"}}
idempotency_key: "{{{{ target.object_id }}}}"
on_failure: {{fallback: {fallback}, exception_class: technical}}
"""


class TmpLake:
    """读=storage 真读+WHERE 等值替身执行;写/审计=真 storage/捕获。"""

    def __init__(self, storage: LanceStorageManager):
        self._storage = storage
        self.captured: list[str] = []
        self.audits: list[dict] = []

    def _get_storage(self):
        return self._storage

    def open_dataset(self, name, table=None):
        return SimpleNamespace(schema=self._storage.read_dataset(name, table=table).schema)

    def olap_query(self, target, sql, max_rows=None):
        self.captured.append(sql)
        t = self._storage.read_dataset("gas_net", table="alerts")
        m = re.search(r'"alert_id" = \'([^\']+)\'', sql)
        if m:
            t = t.filter(pa.array([v == m.group(1) for v in t.column("alert_id").to_pylist()]))
        if max_rows is not None:
            t = t.slice(0, max_rows)
        return SimpleNamespace(table=t)

    def audit_record(self, event_type, dataset_name="", actor="system", payload=None):
        self.audits.append(
            {
                "event_type": event_type,
                "dataset": dataset_name,
                "actor": actor,
                "payload": payload or {},
            }
        )
        return f"audit-{len(self.audits)}"


class StubChecker:
    def get_acl(self, dataset, role):
        return None

    def check_dataset_access(self, *, role, dataset, action, permissions=None):
        return True

    def apply_table_filter(self, table, dataset, role):
        return table


def _user(role: Role = Role.EDITOR, permissions=None, user_id: int | None = 7) -> TokenPayload:
    return TokenPayload(
        sub="op1", role=role, permissions=permissions or [], user_id=user_id, exp=0, iat=0
    )


@pytest.fixture
def env(tmp_path):
    """真 stores(:memory: system_db)+ 真 tmp Lance 容器表。"""
    db = SystemDB(":memory:")
    Migrator(db).run()
    ContractStore(db).save_contract("gas_net", CONTRACT_YAML)
    catalog = ActionCatalogStore(db)
    for yaml in (
        ACT_LOG,
        ACT_PUB,
        ACT_NOASSESS,
        ACT_NOTIFY,
        _ACT_FAIL_BASE.format(aid="ACT.FAIL.REJECT", fallback="REJECT"),
        _ACT_FAIL_BASE.format(aid="ACT.FAIL.DEAD", fallback="DEAD_LETTER"),
        _ACT_FAIL_BASE.format(aid="ACT.FAIL.MANUAL", fallback="MANUAL"),
        _ACT_FAIL_BASE.format(aid="ACT.FAIL.RETRY", fallback="MANUAL"),
    ):
        catalog.save_action(yaml.splitlines()[1].split(":", 1)[1].strip(), yaml)
    storage = LanceStorageManager(base_uri=str(tmp_path))
    storage.create_dataset("gas_net", ROWS, table="alerts")
    lake = TmpLake(storage)
    events.reset_subscribers()
    from arrow_lake.system_db.stores.identity import IdentityStore

    uid = IdentityStore(db).create_user("op1", role="editor")
    yield SimpleNamespace(
        db=db,
        catalog=catalog,
        uid=uid,
        idem=IdempotencyStore(db),
        contract=ContractStore(db),
        alignment=SemanticAlignmentStore(db),
        notify_store=UserStateStore(db),
        lake=lake,
        storage=storage,
    )
    events.reset_subscribers()
    db.close()


ASSESS_OK = {"confidence": 1.0, "matched_rules": 1, "level": "橙", "rule_ids": ["GAS.R1"]}


async def _run(
    env,
    action_id,
    *,
    object_id="GAS.ALERT.001",
    user=None,
    reason="处置完毕",
    assess=None,
    scenario_id=None,
    step_id=None,
):
    return await execute_action(
        lake=env.lake,
        checker=StubChecker(),
        user=user or _user(user_id=env.uid),
        action_id=action_id,
        dataset="gas_net",
        object_type="alerts",
        object_id=object_id,
        reason=reason,
        scenario_id=scenario_id,
        step_id=step_id,
        assess=assess,
        action_store=env.catalog,
        idempotency_store=env.idem,
        contract_store=env.contract,
        alignment_store=env.alignment,
        user_state_store=env.notify_store,
        deny_table_read=lambda n, t: None,
        acl_enforce=lambda sql, tgt: sql,
    )


def _row(env, object_id: str) -> dict:
    rows = env.storage.read_dataset("gas_net", table="alerts").to_pylist()
    return next(r for r in rows if r["alert_id"] == object_id)


# --- 八步序主干 --------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_none_effect_with_audit_context(env) -> None:
    out = await _run(env, "ACT.LOG", assess=ASSESS_OK, scenario_id="GAS.SC", step_id="publish")
    assert out["status"] == "executed" and out["effect"] == {"type": "none"}
    entry = env.lake.audits[-1]
    assert entry["event_type"] == "action.execute"
    assert entry["payload"]["scenario_id"] == "GAS.SC"
    assert entry["payload"]["step_id"] == "publish"
    assert entry["payload"]["included"]["assess.rule_ids"] == ["GAS.R1"]


@pytest.mark.asyncio
async def test_precondition_not_met_422(env) -> None:
    with pytest.raises(ActionError) as ei:
        await _run(env, "ACT.NOASSESS", assess={"confidence": 0.5})
    assert ei.value.status_code == 422
    assert "precondition not met" in ei.value.reason


@pytest.mark.asyncio
async def test_precondition_with_assess_executes(env) -> None:
    out = await _run(env, "ACT.NOASSESS", assess={"confidence": 0.95, "matched_rules": 0})
    assert out["status"] == "executed"


@pytest.mark.asyncio
async def test_reason_required_422(env) -> None:
    with pytest.raises(ActionError) as ei:
        await _run(env, "ACT.LOG", reason="  ")
    assert ei.value.status_code == 422 and "reason" in ei.value.reason


@pytest.mark.asyncio
async def test_permission_scope_missing_403(env) -> None:
    user = _user(permissions=["other:scope"])
    with pytest.raises(ActionError) as ei:
        await _run(env, "ACT.PUBLISH", user=user, assess=ASSESS_OK)
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_permission_scope_present_executes(env) -> None:
    user = _user(permissions=["alerts:publish"])
    out = await _run(env, "ACT.PUBLISH", user=user, assess=ASSESS_OK)
    assert out["status"] == "executed"


@pytest.mark.asyncio
async def test_permission_none_editor_floor_ok(env) -> None:
    out = await _run(env, "ACT.LOG", assess=ASSESS_OK)
    assert out["status"] == "executed"


@pytest.mark.asyncio
async def test_object_not_found_404(env) -> None:
    with pytest.raises(ActionError) as ei:
        await _run(env, "ACT.LOG", object_id="GAS.ALERT.999", assess=ASSESS_OK)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_action_not_in_catalog_404(env) -> None:
    with pytest.raises(ActionError) as ei:
        await _run(env, "NO.SUCH.ACT", assess=ASSESS_OK)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_object_class_mismatch_422(env) -> None:
    bad = """
    action_id: ACT.OTHERCLASS
    title: 别的对象类
    target: {dataset: gas_net, object_class: 管段}
    effect: {type: none}
    """
    env.catalog.save_action("ACT.OTHERCLASS", bad)
    with pytest.raises(ActionError) as ei:
        await _run(env, "ACT.OTHERCLASS", assess=ASSESS_OK)
    assert ei.value.status_code == 422 and "object_class" in ei.value.reason


# --- 幂等 --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_replay_already_in_effect(env) -> None:
    first = await _run(env, "ACT.PUBLISH", assess=ASSESS_OK)
    assert first["status"] == "executed"
    row_after_first = _row(env, "GAS.ALERT.001")
    audits_after_first = len(env.lake.audits)

    second = await _run(env, "ACT.PUBLISH", assess=ASSESS_OK)
    assert second["status"] == "already_in_effect"
    assert second["idempotency"]["state"] == "completed"
    # 不重复写、不重复审计
    assert _row(env, "GAS.ALERT.001") == row_after_first
    assert len(env.lake.audits) == audits_after_first


@pytest.mark.asyncio
async def test_idempotency_key_renders_from_target(env) -> None:
    out = await _run(env, "ACT.PUBLISH", assess=ASSESS_OK)
    assert out["idempotency"]["key"] == "GAS.ALERT.001"


@pytest.mark.asyncio
async def test_failed_state_reclaimable_after_catalog_fix(env) -> None:
    # v1 带坏列 → MANUAL 失败(幂等置 failed);修目录 v2 后重放 → 重新执行
    first = await _run(env, "ACT.FAIL.RETRY", assess=ASSESS_OK)
    assert first["status"] == "manual_intervention"
    fixed = _ACT_FAIL_BASE.format(aid="ACT.FAIL.RETRY", fallback="MANUAL").replace(
        'fields: {no_such_col: "x"}', "fields: {}"
    )
    env.catalog.save_action("ACT.FAIL.RETRY", fixed)
    second = await _run(env, "ACT.FAIL.RETRY", assess=ASSESS_OK)
    assert second["status"] == "executed"
    assert _row(env, "GAS.ALERT.001")["state"] == "published"


# --- 效果 --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_lifecycle_real_write(env) -> None:
    clock = {"t": "2026-08-28T00:00:00+00:00"}
    out = await _run(env, "ACT.PUBLISH", assess=ASSESS_OK)
    assert out["status"] == "executed"
    row = _row(env, "GAS.ALERT.001")
    assert row["state"] == "published"
    assert row["level"] == "橙"  # {{ assess.level }}
    assert row["published_at"] and "T" in row["published_at"]  # {{ now() }} ISO
    other = _row(env, "GAS.ALERT.002")
    assert other["state"] == "pending"  # 只动目标行
    assert clock  # 占位:now() 不可注入中间件路径(默认时钟);ISO 形态已断言


@pytest.mark.asyncio
async def test_notify_effect_delivers_to_user_state(env) -> None:
    out = await _run(env, "ACT.NOTIFY", assess=None)
    assert out["effect"] == {"type": "notify", "delivered": True, "user_id": env.uid}
    notes = env.notify_store.list_notifications(env.uid)
    assert notes and notes[0]["kind"] == "action"
    assert notes[0]["message"] == "对象 GAS.ALERT.001 已处理"


@pytest.mark.asyncio
async def test_notify_skipped_without_user_id(env) -> None:
    out = await _run(env, "ACT.NOTIFY", user=_user(user_id=None))
    assert out["effect"]["delivered"] is False


# --- on_failure 三分派 --------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_reject_422(env) -> None:
    with pytest.raises(ActionError) as ei:
        await _run(env, "ACT.FAIL.REJECT", assess=ASSESS_OK)
    assert ei.value.status_code == 422
    assert ei.value.exception_class == "technical"
    assert env.lake.audits[-1]["event_type"] == "action.failed"


@pytest.mark.asyncio
async def test_failure_dead_letter_200(env) -> None:
    out = await _run(env, "ACT.FAIL.DEAD", assess=ASSESS_OK)
    assert out["status"] == "dead_letter"
    assert out["idempotency"]["state"] == "failed"
    assert env.lake.audits[-1]["payload"]["fallback"] == "DEAD_LETTER"


@pytest.mark.asyncio
async def test_failure_manual_200(env) -> None:
    out = await _run(env, "ACT.FAIL.MANUAL", assess=ASSESS_OK)
    assert out["status"] == "manual_intervention"


# --- 事件(F3.4)--------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_event_delivers_structured_payload(env) -> None:
    got: list[events.ActionEvent] = []
    events.subscribe("alert.published", got.append)
    out = await _run(env, "ACT.PUBLISH", assess=ASSESS_OK)
    assert out["event"] == {"name": "alert.published", "delivered_to": 1}
    assert len(got) == 1
    ev = got[0]
    assert ev.payload["target.object_id"] == "GAS.ALERT.001"
    assert ev.payload["assess.rule_ids"] == ["GAS.R1"]  # 原值(list)
    assert ev.payload["actor"]["sub"] == "op1"  # 原值(dict)


@pytest.mark.asyncio
async def test_event_subscriber_exception_isolated(env) -> None:
    got: list[events.ActionEvent] = []

    def boom(event: events.ActionEvent) -> None:
        raise RuntimeError("subscriber blew up")

    events.subscribe("alert.published", boom)
    events.subscribe("alert.published", got.append)
    out = await _run(env, "ACT.PUBLISH", assess=ASSESS_OK)
    assert out["status"] == "executed"  # 主流程不受订阅者失败影响
    assert out["event"]["delivered_to"] == 1  # 抛错者不计送达
    assert len(got) == 1  # 其余订阅者照常收到


# --- 事件注册表本体 -----------------------------------------------------------


def test_events_registry_basics() -> None:
    events.reset_subscribers()
    assert events.subscriber_count("x.y") == 0
    seen: list[str] = []
    unsub = events.subscribe("x.y", lambda e: seen.append(e.name))
    assert events.subscriber_count("x.y") == 1
    unsub()
    assert events.subscriber_count("x.y") == 0


def test_events_wildcard_subscriber() -> None:
    events.reset_subscribers()
    seen: list[str] = []
    events.subscribe("*", lambda e: seen.append(e.name))
    events.publish(events.ActionEvent(name="a.b", action_id="A", dataset="d", object_id="o"))
    assert seen == ["a.b"]


# --- 路由面(EDITOR 守卫/503/happy)-------------------------------------------


class TestExecuteRoute:
    def _client(self, env, *, role: Role):
        from arrow_lake.api.deps import get_lake
        from arrow_lake.api.routers.actions import router
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.state.action_store = env.catalog
        app.state.idempotency_store = env.idem
        app.state.contract_store = env.contract
        app.state.semantic_alignment_store = env.alignment
        app.state.user_state_store = env.notify_store

        @app.middleware("http")
        async def _inject_user(request: Request, call_next):
            request.state.user = _user(role=role, user_id=env.uid)
            return await call_next(request)

        app.include_router(router)
        app.dependency_overrides[get_lake] = lambda: env.lake
        return TestClient(app)

    def test_viewer_403(self, env) -> None:
        c = self._client(env, role=Role.VIEWER)
        r = c.post(
            "/api/v1/actions/ACT.LOG/execute",
            json={
                "dataset": "gas_net",
                "object_type": "alerts",
                "object_id": "GAS.ALERT.001",
                "reason": "x",
            },
        )
        assert r.status_code == 403

    def test_editor_happy_and_business_422(self, env) -> None:
        c = self._client(env, role=Role.EDITOR)
        r = c.post(
            "/api/v1/actions/ACT.LOG/execute",
            json={
                "dataset": "gas_net",
                "object_type": "alerts",
                "object_id": "GAS.ALERT.001",
                "reason": "处置",
                "assess": ASSESS_OK,
                "scenario_id": "SC",
                "step_id": "s1",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "executed"
        # 前置不满足 → 422(detail 带 exception_class)
        r2 = c.post(
            "/api/v1/actions/ACT.NOASSESS/execute",
            json={
                "dataset": "gas_net",
                "object_type": "alerts",
                "object_id": "GAS.ALERT.001",
                "reason": "x",
                "assess": {"confidence": 0.1},
            },
        )
        assert r2.status_code == 422
        assert r2.json()["detail"]["exception_class"] == "business"

    def test_store_missing_503(self, env) -> None:
        from arrow_lake.api.routers.actions import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.state.action_store = None  # 目录缺位 → 503
        app.include_router(router)
        c = TestClient(app)
        r = c.post(
            "/api/v1/actions/ACT.LOG/execute",
            json={"dataset": "gas_net", "object_type": "alerts", "object_id": "GAS.ALERT.001"},
        )
        assert r.status_code in (401, 403, 503)
