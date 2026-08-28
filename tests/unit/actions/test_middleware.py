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
from arrow_lake.system_db.stores.ontology import OntologyRulesStore
from arrow_lake.system_db.stores.scenarios import ScenarioStore
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

# H-3:前置引用 canonical 字段,但阈值高于服务端重评可达值——客户端伪造无法通过
ACT_NEED5 = """
action_id: ACT.NEED5
title: 需五条命中
target: {dataset: gas_net, object_class: 告警事件}
preconditions: ["assess.matched_rules >= 5"]
effect: {type: none}
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
    rules = OntologyRulesStore(db)
    rules.upsert_rule(
        "ACT.R.STATE",
        scope="gas_net",
        condition_expr="target.state == 'pending'",
        conclusion="待处置",
        source_ref="w4t",
        rule_type="validation",
    )
    rules.transition("ACT.R.STATE", "active")
    catalog = ActionCatalogStore(db)
    for yaml in (
        ACT_LOG,
        ACT_PUB,
        ACT_NOASSESS,
        ACT_NOTIFY,
        ACT_NEED5,
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
        rules=rules,
        scenarios=ScenarioStore(db),
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
    checker=None,
    deny_write=None,
):
    return await execute_action(
        lake=env.lake,
        checker=checker or StubChecker(),
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
        rules_store=env.rules,
        scenario_store=env.scenarios,
        deny_table_read=lambda n, t: None,
        acl_enforce=lambda sql, tgt: sql,
        deny_table_write=deny_write,
    )


def _row(env, object_id: str) -> dict:
    rows = env.storage.read_dataset("gas_net", table="alerts").to_pylist()
    return next(r for r in rows if r["alert_id"] == object_id)


# --- 八步序主干 --------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_none_effect_with_audit_context(env) -> None:
    env.scenarios.save_scenario(
        "GAS.SC", "scenario_id: GAS.SC\ntitle: t\nsteps:\n  - {id: publish, action: ACT.LOG}\n"
    )
    out = await _run(env, "ACT.LOG", assess=ASSESS_OK, scenario_id="GAS.SC", step_id="publish")
    assert out["status"] == "executed" and out["effect"] == {"type": "none"}
    entry = env.lake.audits[-1]
    assert entry["event_type"] == "action.execute"
    assert entry["payload"]["scenario_id"] == "GAS.SC"
    assert entry["payload"]["step_id"] == "publish"
    assert entry["payload"]["included"]["assess.rule_ids"] == ["ACT.R.STATE"]  # 服务端重评(H-3)


@pytest.mark.asyncio
async def test_precondition_not_met_422(env) -> None:
    with pytest.raises(ActionError) as ei:
        await _run(env, "ACT.NEED5", assess={"matched_rules": 5})  # 伪造无效(H-3)
    assert ei.value.status_code == 422
    assert "precondition not met" in ei.value.reason


@pytest.mark.asyncio
async def test_server_recomputed_confidence_executes(env) -> None:
    out = await _run(env, "ACT.NOASSESS")  # 无客户端 assess;服务端 confidence=1.0
    assert out["status"] == "executed"
    assert out["assess_recomputed"]["rule_ids"] == ["ACT.R.STATE"]


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
    # H-3:{{ assess.level }} 属 canonical 四字段之外自造键,服务端重评不
    # 提供 → 缺失渲染空串(fail-safe);可用的是 assess.rule_ids 等 canonical
    assert row["level"] == ""
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
    assert ev.payload["assess.rule_ids"] == ["ACT.R.STATE"]  # 原值(list),服务端重评
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
        # 前置不满足 → 422(detail 带 exception_class;伪造客户端 assess 无效 H-3)
        r2 = c.post(
            "/api/v1/actions/ACT.NEED5/execute",
            json={
                "dataset": "gas_net",
                "object_type": "alerts",
                "object_id": "GAS.ALERT.001",
                "reason": "x",
                "assess": {"matched_rules": 5},
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


# ---------------------------------------------------------------------------
# W4.5 review 清偿专项(两路 fan-out:安全 3H/3M/2L + 正确性 1H/4M/2L)
# ---------------------------------------------------------------------------


class TestReviewRemediation:
    """H-1 写向门禁 / H-2·正确性HIGH 前置槽泄漏 / H-3 assess 伪造 /
    M-1 标识不唯一 / M-3 审计兜底 / M-4 to_state 词表 / M-2 超时禁重放 /
    L-1 场景归属 / reset 运维面。"""

    # -- H-1:写向门禁 ------------------------------------------------------

    @pytest.mark.asyncio
    async def test_h1_write_denied_dataset_403(self, env) -> None:
        class WriteDenied(StubChecker):
            def check_dataset_access(self, *, role, dataset, action, permissions=None):
                return action == "read"

        with pytest.raises(ActionError) as ei:
            await _run(
                env,
                "ACT.PUBLISH",
                checker=WriteDenied(),
                user=_user(permissions=["alerts:publish"]),
            )
        assert ei.value.status_code == 403
        assert "Write access" in ei.value.reason

    @pytest.mark.asyncio
    async def test_h1_table_write_deny_403(self, env) -> None:
        from fastapi import HTTPException

        def deny_write(name, table):
            raise HTTPException(status_code=403, detail="table write deny")

        with pytest.raises(HTTPException):
            await _run(
                env,
                "ACT.PUBLISH",
                deny_write=deny_write,
                user=_user(permissions=["alerts:publish"]),
            )
        # 效果未落:目标行状态不变
        assert _row(env, "GAS.ALERT.001")["state"] == "pending"

    @pytest.mark.asyncio
    async def test_h1_readonly_effect_no_write_gate(self, env) -> None:
        """none/notify 效果不触发写向门禁(无物理写)。"""
        out = await _run(env, "ACT.LOG", checker=StubChecker())
        assert out["status"] == "executed"

    # -- H-2(+正确性 HIGH):前置失败后槽置 failed,可重认领 ------------------

    @pytest.mark.asyncio
    async def test_h2_precondition_failure_marks_slot_failed(self, env) -> None:
        # 001 当前 pending;前置要求 confirmed → 不满足
        env.catalog.save_action(
            "ACT.NEEDCONF",
            """
action_id: ACT.NEEDCONF
title: 需确认态
target: {dataset: gas_net, object_class: 告警事件}
preconditions: ["target.state == 'confirmed'"]
effect: {type: none}
idempotency_key: "{{ target.object_id }}"
""",
        )
        with pytest.raises(ActionError):
            await _run(env, "ACT.NEEDCONF")
        rec = env.idem.get("ACT.NEEDCONF", "GAS.ALERT.001")
        assert rec["state"] == "failed"  # 非 running:可重认领
        assert "precondition" in (rec["detail"] or "")
        # 目录修复(前置改为 pending)后重放 → 执行成功
        env.catalog.save_action(
            "ACT.NEEDCONF",
            """
action_id: ACT.NEEDCONF
title: 需确认态
target: {dataset: gas_net, object_class: 告警事件}
preconditions: ["target.state == 'pending'"]
effect: {type: none}
idempotency_key: "{{ target.object_id }}"
""",
        )
        out = await _run(env, "ACT.NEEDCONF")
        assert out["status"] == "executed"

    # -- H-3:客户端 assess 伪造无效 ----------------------------------------

    @pytest.mark.asyncio
    async def test_h3_forged_assess_cannot_pass(self, env) -> None:
        with pytest.raises(ActionError) as ei:
            await _run(env, "ACT.NEED5", assess={"matched_rules": 5})
        assert "precondition not met" in ei.value.reason

    @pytest.mark.asyncio
    async def test_h3_response_carries_recomputed_assess(self, env) -> None:
        out = await _run(env, "ACT.LOG", assess={"matched_rules": 999})
        assert out["assess_recomputed"]["matched_rules"] == 1
        assert out["assess_recomputed"]["rule_ids"] == ["ACT.R.STATE"]

    # -- M-1:标识列不唯一 → 拒绝执行 ----------------------------------------

    @pytest.mark.asyncio
    async def test_m1_duplicate_identifier_422(self, env) -> None:
        env.storage.append_dataset(
            "gas_net",
            pa.table(
                {
                    "alert_id": ["GAS.ALERT.001"],
                    "state": ["pending"],
                    "level": pa.array([None], pa.string()),
                    "published_at": pa.array([None], pa.string()),
                }
            ),
            table="alerts",
        )
        with pytest.raises(ActionError) as ei:
            await _run(env, "ACT.LOG")
        assert ei.value.status_code == 422
        assert "not unique" in ei.value.reason

    # -- M-3:审计失败不吞效果事实 -------------------------------------------

    @pytest.mark.asyncio
    async def test_m3_audit_failure_keeps_effect_truth(self, env) -> None:
        def boom(*a, **k):
            raise RuntimeError("audit store down")

        env.lake.audit_record = boom  # type: ignore[method-assign]
        user = _user(permissions=["alerts:publish"])
        out = await _run(env, "ACT.PUBLISH", user=user)  # ACT.PUB 带幂等键
        assert out["status"] == "executed"
        assert out["audit_id"] is None and out["audit_status"] == "failed"
        assert out["idempotency"]["state"] == "completed"  # 效果真值优先
        # 重放 already_in_effect,不双写(to_state 同值,fields now() 不再漂移)
        replay = await _run(env, "ACT.PUBLISH", user=user)
        assert replay["status"] == "already_in_effect"

    # -- M-4:to_state 必须落在契约 lifecycle 词表 ---------------------------

    @pytest.mark.asyncio
    async def test_m4_to_state_outside_vocabulary_422(self, env) -> None:
        env.catalog.save_action(
            "ACT.BADSTATE",
            """
action_id: ACT.BADSTATE
title: 拼错状态
target: {dataset: gas_net, object_class: 告警事件}
effect: {type: update_lifecycle, to_state: pubished}
idempotency_key: "{{ target.object_id }}"
""",
        )
        with pytest.raises(ActionError) as ei:
            await _run(env, "ACT.BADSTATE")
        assert "not in contract lifecycle states" in ei.value.reason
        assert _row(env, "GAS.ALERT.001")["state"] == "pending"  # 未写

    # -- M-2:超时类失败禁自动重放(强制 manual_intervention) ----------------

    @pytest.mark.asyncio
    async def test_m2_timeout_forces_manual_intervention(self, env, monkeypatch) -> None:
        real_update = env.storage.update_rows

        def slow_update(*a, **k):
            raise TimeoutError("run_sync watchdog")

        monkeypatch.setattr(env.storage, "update_rows", slow_update)
        out = await _run(
            env,
            "ACT.FAIL.DEAD",  # 配置是 DEAD_LETTER
            user=_user(permissions=["alerts:publish"]),
        )
        assert out["status"] == "manual_intervention"  # 超时覆盖 fallback
        assert out["idempotency"]["state"] == "failed"
        monkeypatch.setattr(env.storage, "update_rows", real_update)

    # -- L-1:场景归属校验 ---------------------------------------------------

    @pytest.mark.asyncio
    async def test_l1_ghost_scenario_422(self, env) -> None:
        with pytest.raises(ActionError) as ei:
            await _run(env, "ACT.LOG", scenario_id="GHOST.SC", step_id="x")
        assert ei.value.status_code == 422

    @pytest.mark.asyncio
    async def test_l1_step_must_exist_in_scenario(self, env) -> None:
        env.scenarios.save_scenario(
            "GAS.SC2",
            """
scenario_id: GAS.SC2
title: t
steps:
  - {id: publish, action: ACT.LOG}
""",
        )
        with pytest.raises(ActionError) as ei:
            await _run(env, "ACT.LOG", scenario_id="GAS.SC2", step_id="ghost_step")
        assert "not in scenario" in ei.value.reason
        out = await _run(env, "ACT.LOG", scenario_id="GAS.SC2", step_id="publish")
        assert out["status"] == "executed"

    @pytest.mark.asyncio
    async def test_l1_step_without_scenario_422(self, env) -> None:
        with pytest.raises(ActionError) as ei:
            await _run(env, "ACT.LOG", step_id="orphan")
        assert "requires scenario_id" in ei.value.reason

    # -- H-2 运维面:running 槽 admin 重置 -----------------------------------

    def test_reset_running_slot(self, env) -> None:
        env.idem.try_acquire("ACT.X", "K", owner="dead-worker")
        assert env.idem.get("ACT.X", "K")["state"] == "running"
        assert env.idem.reset_running("ACT.X", "K") is True
        rec = env.idem.get("ACT.X", "K")
        assert rec["state"] == "failed" and "admin reset" in rec["detail"]
        # completed 槽不可重置(已生效事实不容否认)
        env.idem.mark("ACT.X", "K", "completed")
        assert env.idem.reset_running("ACT.X", "K") is False
