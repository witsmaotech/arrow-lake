"""W3.2/W3.3/W3.4 — POST /api/v1/decisions/assess(研判引擎+路由+黄金集)。

契约(实施计划 §3 W3):
* VIEWER(S9);对象读取经 W3.1 共享管线——dataset 读权 403/无契约 422
  (S8 同语义)/未知 object_type 422/对象不存在 404,与 objects 同路;
* 规则求值:scope=数据集 + ``*`` 的 active 规则;命中/未命中/无规则;
* **unruly(S8)**:condition_expr 编译失败 → unruly 列表,不炸研判;
* actionable:行动目录反查(dataset+object_class 匹配且前置为真);
* **S6 对齐后口径**:谓词比较的是对齐投影后的值(MPa→kPa);
* 黄金集(W3.4):规则×对象 → 期望命中集合,≥10 对(5 对象 × 每对
  多条正/负规则期望)。
"""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.actions import ActionCatalogStore
from arrow_lake.system_db.stores.contracts import ContractStore
from arrow_lake.system_db.stores.ontology import OntologyRulesStore
from arrow_lake.system_db.stores.semantic_alignments import SemanticAlignmentStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

CONTRACT_YAML = """
dataset: gas_net
tables:
  alerts:
    object_class: 告警事件
    lifecycle: {column: 状态, states: [待研判, 研判确认, 已发布, 已关闭], initial: 待研判}
    identifier:
      column: alert_id
      pattern: "GAS.ALERT.{序号}"
    columns:
      - {name: 压力, label: 泄漏压力, unit: kPa}
      - {name: 风险等级}
"""

# 黄金集对象:stub 返回**执行后形态**(对齐后口径)。raw MPa ×1000 → kPa;
# 对齐算术本身由 test_objectset/test_objects_e2e 的真管线覆盖,此处钉住
# 「研判吃到的就是对齐后值」+ 捕获 SQL 含投影(见 S6 用例)。
GOLDEN_TABLE = pa.table(
    {
        "alert_id": [
            "GAS.ALERT.001",
            "GAS.ALERT.002",
            "GAS.ALERT.003",
            "GAS.ALERT.004",
            "GAS.ALERT.005",
        ],
        "压力": [2000.0, 900.0, 3500.0, 100.0, 2000.0],  # kPa(aligned)
        "风险等级": ["高", "中", "极高", "低", "中"],
        "状态": ["待研判", "已发布", "待研判", "已关闭", "待研判"],
    }
)

# 黄金集期望(W3.4):object_id → 命中 rule_id 集合(未列出的即负例)
GOLDEN_EXPECTED: dict[str, set[str]] = {
    "GAS.ALERT.001": {"GAS.R.HIGH", "GAS.R.STATE_OPEN", "GAS.R.LEVEL_HIGH"},
    "GAS.ALERT.002": {"GAS.R.MID"},
    "GAS.ALERT.003": {"GAS.R.HIGH", "GAS.R.GLOBAL", "GAS.R.STATE_OPEN", "GAS.R.LEVEL_HIGH"},
    "GAS.ALERT.004": set(),
    "GAS.ALERT.005": {"GAS.R.HIGH", "GAS.R.STATE_OPEN"},
}

ALIGN_YAML = """
dataset: gas_net
tables:
  alerts:
    columns:
      压力: {unit: {from: MPa, to: kPa}}
"""


class StubLake:
    """执行保真 stub:对 ``"alert_id" = '...'`` 形态的 WHERE 等值过滤照办
    (duckdb 执行语义的最小替身),其余投影/limit 不做。"""

    def __init__(self, tables: dict[str, pa.Table], container: bool = True):
        self._tables = tables
        self._container = container
        self.captured: list[tuple[str, str]] = []

    def _get_storage(self):
        return SimpleNamespace(
            list_container_tables=lambda n: ["alerts"] if self._container else []
        )

    def open_dataset(self, name, table=None):
        key = table or name
        return SimpleNamespace(schema=self._tables[key].schema)

    def olap_query(self, target, sql, max_rows=None):
        import re

        self.captured.append((target, sql))
        table = self._tables[target.split(".")[-1]]
        m = re.search(r'"alert_id" = \'([^\']+)\'', sql)
        if m:
            mask = pa.array([v == m.group(1) for v in table.column("alert_id").to_pylist()])
            table = table.filter(mask)
        if max_rows is not None:
            table = table.slice(0, max_rows)
        return SimpleNamespace(table=table, sql=sql)


class StubChecker:
    def __init__(self, allowed: bool = True):
        self.allowed = allowed

    def get_acl(self, dataset, role):
        return None

    def check_dataset_access(self, *, role, dataset, action, permissions=None):
        return self.allowed

    def apply_table_filter(self, table, dataset, role):
        return table


ACTION_PUBLISH = """
action_id: ACT.GAS.PUBLISH
title: 发布预警
target: {dataset: gas_net, object_class: 告警事件}
preconditions:
  - "assess.matched_rules >= 1"
effect: {type: none}
"""

ACTION_ALWAYS = """
action_id: ACT.GAS.LOG
title: 登记
target: {dataset: gas_net, object_class: 告警事件}
effect: {type: none}
"""

ACTION_CONFIRMED_ONLY = """
action_id: ACT.GAS.CONFIRM
title: 需研判确认态
target: {dataset: gas_net, object_class: 告警事件}
preconditions:
  - "target.lifecycle_state == '研判确认'"
effect: {type: none}
"""

ACTION_OTHER_DS = ACTION_PUBLISH.replace("ACT.GAS.PUBLISH", "ACT.OTHER.PUBLISH").replace(
    "dataset: gas_net", "dataset: other_net"
)
ACTION_OTHER_CLASS = ACTION_PUBLISH.replace("ACT.GAS.PUBLISH", "ACT.GAS.OTHERCLASS").replace(
    "object_class: 告警事件", "object_class: 管段"
)


def _seed_rules(db: SystemDB) -> None:
    rules = OntologyRulesStore(db)
    rules.upsert_rule(
        "GAS.R.HIGH",
        scope="gas_net",
        condition_expr="target.压力 >= 1500",
        conclusion="高压泄漏告警",
        source_ref="spec-A1",
        rule_type="risk_control",
    )
    rules.transition("GAS.R.HIGH", "active")
    rules.upsert_rule(
        "GAS.R.MID",
        scope="gas_net",
        condition_expr="target.压力 >= 500 && target.压力 < 1500",
        conclusion="中压关注",
        source_ref="spec-A2",
        rule_type="risk_control",
    )
    rules.transition("GAS.R.MID", "active")
    rules.upsert_rule(
        "GAS.R.GLOBAL",
        scope="*",
        condition_expr="target.压力 >= 3000",
        conclusion="极高压(全局规则)",
        source_ref="spec-G",
        rule_type="risk_control",
    )
    rules.transition("GAS.R.GLOBAL", "active")
    rules.upsert_rule(
        "GAS.R.STATE_OPEN",
        scope="gas_net",
        condition_expr="target.lifecycle_state == '待研判'",
        conclusion="待研判处置",
        source_ref="spec-B1",
        rule_type="validation",
    )
    rules.transition("GAS.R.STATE_OPEN", "active")
    rules.upsert_rule(
        "GAS.R.LEVEL_HIGH",
        scope="gas_net",
        condition_expr="target.风险等级 in ['高', '极高']",
        conclusion="高风险等级",
        source_ref="spec-B2",
        rule_type="risk_control",
    )
    rules.transition("GAS.R.LEVEL_HIGH", "active")
    rules.upsert_rule(
        "GAS.R.NOMATCH",
        scope="gas_net",
        condition_expr="target.压力 < 10",
        conclusion="永不命中(负例)",
        source_ref="spec-N",
    )
    rules.transition("GAS.R.NOMATCH", "active")
    rules.upsert_rule(
        "GAS.R.DRAFT",
        scope="gas_net",
        condition_expr="target.压力 >= 1",
        conclusion="草稿不出现",
        source_ref="spec-D",
    )
    # GAS.R.DRAFT 保持 draft


def _seed_actions(db: SystemDB) -> None:
    store = ActionCatalogStore(db)
    for yaml in (
        ACTION_PUBLISH,
        ACTION_ALWAYS,
        ACTION_CONFIRMED_ONLY,
        ACTION_OTHER_DS,
        ACTION_OTHER_CLASS,
    ):
        store.save_action(yaml.splitlines()[1].split(":", 1)[1].strip(), yaml)


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    ContractStore(conn).save_contract("gas_net", CONTRACT_YAML)
    SemanticAlignmentStore(conn).save_alignment("gas_net", ALIGN_YAML)
    _seed_rules(db=conn)
    _seed_actions(db=conn)
    yield conn
    conn.close()


def _client(
    db: SystemDB | None,
    *,
    lake: StubLake | None = None,
    checker: StubChecker | None = None,
    role: Role = Role.VIEWER,
    rules_store: object = ...,  # ... = 按 db 正常建;None = 模拟缺 store
    inject_user: bool = True,
) -> TestClient:
    from arrow_lake.api.deps import get_checker as get_checker_dep
    from arrow_lake.api.deps import get_lake as get_lake_dep
    from arrow_lake.api.routers.decisions import router

    app = FastAPI()
    app.state.contract_store = ContractStore(db) if db else None
    app.state.semantic_alignment_store = SemanticAlignmentStore(db) if db else None
    app.state.ontology_rules_store = (
        OntologyRulesStore(db) if (db is not None and rules_store is ...) else rules_store
    )
    app.state.action_store = ActionCatalogStore(db) if db else None

    if inject_user:

        @app.middleware("http")
        async def _inject_user(request: Request, call_next):
            request.state.user = TokenPayload(sub="tester", role=role, exp=0, iat=0)
            return await call_next(request)

    app.include_router(router)
    app.dependency_overrides[get_lake_dep] = lambda: lake or StubLake({"alerts": GOLDEN_TABLE})
    if checker is not None:
        app.dependency_overrides[get_checker_dep] = lambda: checker
    return TestClient(app)


def _assess(
    client: TestClient, object_id: str, dataset: str = "gas_net", object_type: str = "alerts"
):
    return client.post(
        "/api/v1/decisions/assess",
        json={
            "dataset": dataset,
            "object_type": object_type,
            "object_id": object_id,
        },
    )


# --- golden set(W3.4)-------------------------------------------------------


@pytest.mark.parametrize("object_id,expected", sorted(GOLDEN_EXPECTED.items()))
def test_golden_set(object_id: str, expected: set[str], db: SystemDB) -> None:
    """黄金集:5 对象 × 7 条规则(含负例/全局/未命中)→ 期望命中集合。"""
    r = _assess(_client(db), object_id)
    assert r.status_code == 200, r.text
    body = r.json()
    matched = {c["rule_id"] for c in body["conclusions"]}
    assert matched == expected
    assert body["matched_rules"] == len(expected)
    assert body["confidence"] == 1.0
    # 全部 active 结论条目携带四元组
    for c in body["conclusions"]:
        assert {"rule_id", "rule_type", "version", "conclusion"} <= set(c)


# --- engine semantics --------------------------------------------------------


def test_unruly_rule_marked_not_fatal(db: SystemDB) -> None:
    rules = OntologyRulesStore(db)
    rules.upsert_rule(
        "GAS.R.UNRULY",
        scope="gas_net",
        condition_expr="压力 >= 1500 &&",  # 尾随 && → 不可编译
        conclusion="坏规则",
        source_ref="bad",
    )
    rules.transition("GAS.R.UNRULY", "active")
    r = _assess(_client(db), "GAS.ALERT.001")
    assert r.status_code == 200
    body = r.json()
    assert body["unruly"] == ["GAS.R.UNRULY"]
    assert {c["rule_id"] for c in body["conclusions"]} == GOLDEN_EXPECTED[
        "GAS.ALERT.001"
    ]  # 其余规则照常求值


def test_no_rules_matched_confidence_still_one(db: SystemDB) -> None:
    """S10:确定性规则 confidence 恒 1.0(未命中也不断言不确定性)。"""
    body = _assess(_client(db), "GAS.ALERT.004").json()
    assert body["matched_rules"] == 0
    assert body["conclusions"] == []
    assert body["confidence"] == 1.0


def test_actionable_from_catalog(db: SystemDB) -> None:
    body = _assess(_client(db), "GAS.ALERT.001").json()
    assert set(body["actionable"]) == {"ACT.GAS.PUBLISH", "ACT.GAS.LOG"}


def test_actionable_precondition_blocks(db: SystemDB) -> None:
    # 004 无命中 → matched_rules=0 → PUBLISH 前置不满足;LOG(无前置)恒在
    body = _assess(_client(db), "GAS.ALERT.004").json()
    assert body["actionable"] == ["ACT.GAS.LOG"]


def test_actionable_lifecycle_precondition(db: SystemDB) -> None:
    # CONFIRM 需 '研判确认' 态;三行数据均不满足 → 从不出现在 actionable
    for oid in GOLDEN_EXPECTED:
        body = _assess(_client(db), oid).json()
        assert "ACT.GAS.CONFIRM" not in body["actionable"]


def test_lifecycle_state_in_context(db: SystemDB) -> None:
    body = _assess(_client(db), "GAS.ALERT.001").json()
    assert body["lifecycle_state"] == "待研判"


# --- S6 对齐后口径 ------------------------------------------------------------


def test_aligned_caliber_feeds_predicates(db: SystemDB) -> None:
    """S6:谓词比较对齐投影后的值——捕获 SQL 必含 kPa 投影(MPa×1000),
    且 002(900 kPa)只命中 MID(>= 500 && < 1500)。"""
    lake = StubLake({"alerts": GOLDEN_TABLE})
    body = _assess(_client(db, lake=lake), "GAS.ALERT.001").json()
    assert any(c["rule_id"] == "GAS.R.HIGH" for c in body["conclusions"])
    assert '("压力" * 1000.0)' in lake.captured[0][1]  # 对齐投影已进 SQL
    body2 = _assess(_client(db), "GAS.ALERT.002").json()
    assert {c["rule_id"] for c in body2["conclusions"]} == {"GAS.R.MID"}


# --- RBAC / error semantics(与 objects 同路)--------------------------------


def test_dataset_read_denied_403(db: SystemDB) -> None:
    client = _client(db, checker=StubChecker(allowed=False))
    assert _assess(client, "GAS.ALERT.001").status_code == 403


def test_no_contract_422_s8(db: SystemDB) -> None:
    r = _assess(_client(db), "GAS.ALERT.001", dataset="ghost_ds")
    assert r.status_code == 422
    assert "no contract" in r.json()["detail"]


def test_unknown_object_type_422(db: SystemDB) -> None:
    assert _assess(_client(db), "GAS.ALERT.001", object_type="ghost").status_code == 422


def test_object_not_found_404(db: SystemDB) -> None:
    r = _assess(_client(db), "GAS.ALERT.999")
    assert r.status_code == 404


def test_rules_store_missing_503() -> None:
    client = _client(None, rules_store=None)
    assert _assess(client, "GAS.ALERT.001").status_code == 503


def test_no_credentials_401(db: SystemDB) -> None:
    """无凭证(不注入 user)→ 401:路由守卫在位。"""
    client = _client(db, inject_user=False)
    assert _assess(client, "GAS.ALERT.001").status_code in (401, 403)
