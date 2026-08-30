"""W1.5 — /api/v1/quality/assess + /quality/reports(v1.11.4 MS5)。

契约(实施计划 §3 W1.5):
* 全部 ADMIN(非 admin → 403);system_db 关闭 → 503;
* assess:读契约(quality 节)+ 数据集 + 死信 + ADL → 五维评分 → 落
  sys_quality_reports;audit(``quality.assess``)+ lineage
  (``quality.assessed``)best-effort 回带 recorded 标志;
* 数据集不可读 → 404;reports 历史 newest-first。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pyarrow as pa
import pytest
from arrow_lake.annotation.adl import ADL_SCHEMA
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.api.deps import get_lake
from arrow_lake.api.routers.quality_report import router
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.contracts import ContractStore
from arrow_lake.system_db.stores.quality_reports import QualityReportStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
_TS = pa.timestamp("s")

CONTRACT = """
dataset: alerts
tables:
  alerts:
    columns:
      - name: severity
        required: true
        enum: [high, low]
"""


class FakeLake:
    """最小只读 Lake:read_dataset / audit / lineage 记调用 + ADL 写面。"""

    def __init__(self, tables: dict[str, pa.Table]) -> None:
        self._tables = tables
        self.audit_calls: list[tuple[str, dict]] = []
        self.lineage_calls: list[tuple[str, str]] = []

    def read_dataset(self, name: str, columns=None, table=None):
        if name not in self._tables:
            raise KeyError(name)
        return self._tables[name]

    def audit_record(self, event_type: str, **kw) -> str:
        self.audit_calls.append((event_type, kw))
        return "audit-1"

    def lineage_record_event(self, dataset: str, operation: str, **kw) -> None:
        self.lineage_calls.append((dataset, operation))

    # --- llm_only 相关性直评的写面(ADL create-or-append) ---
    def _get_storage(self) -> FakeLake:
        return self

    def dataset_exists(self, name: str) -> bool:
        return name in self._tables

    def create_dataset(self, name: str, table: pa.Table) -> None:
        self._tables[name] = table

    def append_dataset(self, name: str, table: pa.Table) -> None:
        self._tables[name] = pa.concat_tables([self._tables[name], table])


def _source_table(n: int = 10) -> pa.Table:
    return pa.Table.from_pylist([
        {
            "severity": "high" if i % 2 == 0 else "low",
            "text": f"alert {i}",
            "updated_at": int((NOW - timedelta(hours=1)).timestamp()),
        }
        for i in range(n)
    ], schema=pa.schema([
        ("severity", pa.string()), ("text", pa.string()), ("updated_at", _TS),
    ]))


def _adl(n: int = 4) -> pa.Table:
    rows = []
    for i in range(n):
        span = [{"label": "阀门", "start": 0, "end": 2}]
        for annotator in ("ann1", "ann2"):  # 完美双标注一致 → κ=1
            rows.append({
                "adl_id": f"r{i}-{annotator}", "source_dataset": "alerts",
                "source_row_id": f"r{i}", "objects": span, "events": [],
                "rules_applied": [], "scenario": "s1", "relations": [],
                "annotator_id": annotator,
                "annotated_at": NOW.isoformat(),
                "review_status": "approved", "reviewer_id": "",
                "batch_id": "b1", "adl_version": 1,
            })
    return pa.Table.from_pylist(rows, schema=ADL_SCHEMA)


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


def _make_app(
    *, role: Role, db: SystemDB | None, lake: FakeLake | None,
    relevance_provider: Any | None = None,
) -> TestClient:
    from arrow_lake.system_db.stores.annotation import AnnotationProjectStore
    from arrow_lake.system_db.stores.drift_baselines import DriftBaselineStore

    app = FastAPI()
    app.state.quality_report_store = QualityReportStore(db) if db else None
    app.state.contract_store = ContractStore(db) if db else None
    app.state.drift_baseline_store = DriftBaselineStore(db) if db else None
    app.state.annotation_project_store = (
        AnnotationProjectStore(db) if db else None)
    app.state.relevance_provider = relevance_provider

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(sub="tester", role=role, exp=0, iat=0)
        return await call_next(request)

    if lake is not None:
        app.dependency_overrides[get_lake] = lambda: lake
    app.include_router(router)
    return TestClient(app)


def _ready(db: SystemDB) -> tuple[SystemDB, FakeLake]:
    ContractStore(db).save_contract("alerts", CONTRACT)
    return db, FakeLake({"alerts": _source_table(), "alerts_adl": _adl()})


# --- 访问控制 / 降级 ---------------------------------------------------------

def test_non_admin_403(db: SystemDB) -> None:
    _, lake = _ready(db)
    client = _make_app(role=Role.VIEWER, db=db, lake=lake)
    assert client.post("/api/v1/quality/assess/alerts").status_code == 403
    assert client.get("/api/v1/quality/reports/alerts").status_code == 403


def test_store_missing_503() -> None:
    client = _make_app(role=Role.ADMIN, db=None, lake=FakeLake({}))
    assert client.post("/api/v1/quality/assess/alerts").status_code == 503
    assert client.get("/api/v1/quality/reports/alerts").status_code == 503


# --- assess 主链 -------------------------------------------------------------

def test_assess_happy_path_persists_and_audits(db: SystemDB) -> None:
    _, lake = _ready(db)
    client = _make_app(role=Role.ADMIN, db=db, lake=lake)
    r = client.post("/api/v1/quality/assess/alerts")
    assert r.status_code == 200, r.text
    body = r.json()
    # accuracy(κ=1→100)/completeness(100)/diversity(均匀二类→50)/
    # timeliness(新鲜度 1h≤72→100)重归一:(35+20+7.5+10)/0.8 = 90.625
    assert body["total_score"] == 90.625
    assert body["star"] == 4 and body["admission"] == "bronze"  # 85≤90.625<95
    assert body["verdict"] == "degraded"  # relevance 未接线(W2)
    assert body["degraded"] == ["relevance"]
    assert set(body["dimensions"]) == {
        "relevance", "accuracy", "completeness", "diversity", "timeliness"}
    assert body["dimensions"]["accuracy"]["score"] == 100.0
    assert body["audit_recorded"] is True and body["lineage_recorded"] is True
    assert (lake.audit_calls[0][0],) == ("quality.assess",)
    assert lake.lineage_calls[0][:2] == ("alerts", "quality.assessed")


def test_assess_report_persisted_with_spec(db: SystemDB) -> None:
    _, lake = _ready(db)
    client = _make_app(role=Role.ADMIN, db=db, lake=lake)
    client.post("/api/v1/quality/assess/alerts")
    reports = client.get("/api/v1/quality/reports/alerts").json()
    assert reports["total"] == 1
    latest = reports["latest"]
    assert latest["total_score"] == 90.625
    assert latest["spec"]["weights"]["accuracy"] == 0.35
    assert latest["assessed_by"] == "tester"
    assert latest["dimensions"]["completeness"]["details"]["checks"]


def test_assess_unknown_dataset_404(db: SystemDB) -> None:
    lake = FakeLake({})
    client = _make_app(role=Role.ADMIN, db=db, lake=lake)
    assert client.post("/api/v1/quality/assess/ghost").status_code == 404


def test_reports_history_newest_first(db: SystemDB) -> None:
    _, lake = _ready(db)
    client = _make_app(role=Role.ADMIN, db=db, lake=lake)
    client.post("/api/v1/quality/assess/alerts")
    # 第二轮:删 ADL → accuracy 降级 → 总分变化
    lake._tables.pop("alerts_adl")
    client.post("/api/v1/quality/assess/alerts")
    reports = client.get("/api/v1/quality/reports/alerts").json()
    assert reports["total"] == 2
    scores = [r["total_score"] for r in reports["reports"]]
    assert scores[0] != scores[1]  # newest first:两轮分不同
    assert reports["reports"][0]["degraded"] == ["accuracy", "relevance"]


def test_reports_empty_dataset_200(db: SystemDB) -> None:
    lake = FakeLake({})
    client = _make_app(role=Role.ADMIN, db=db, lake=lake)
    body = client.get("/api/v1/quality/reports/ghost").json()
    assert body["total"] == 0 and body["latest"] is None


def test_assess_contract_quality_node_flows(db: SystemDB) -> None:
    ContractStore(db).save_contract("alerts", CONTRACT + """
quality:
  critical: true
""")
    lake = FakeLake({"alerts": _source_table(), "alerts_adl": _adl()})
    client = _make_app(role=Role.ADMIN, db=db, lake=lake)
    body = client.post("/api/v1/quality/assess/alerts").json()
    # critical → 准确性门槛 95;κ=100 不否决,但 spec 快照要带 critical
    assert body["verdict"] == "degraded"
    latest = client.get("/api/v1/quality/reports/alerts").json()["latest"]
    assert latest["spec"]["critical"] is True
    assert latest["spec"]["thresholds"]["accuracy"] == 95


# === W2 drift(POST /quality/drift/{ds}) =====================================

def _skewed_table() -> pa.Table:
    rows = [
        {"severity": "high" if i < 9 else "low",  # 90/10 偏移(基线 50/50)
         "text": f"alert {i}", "updated_at": 1}
        for i in range(10)
    ]
    return pa.Table.from_pylist(rows, schema=pa.schema([
        ("severity", pa.string()), ("text", pa.string()),
        ("updated_at", _TS),
    ]))


def test_drift_first_call_creates_baseline(db: SystemDB) -> None:
    _, lake = _ready(db)
    client = _make_app(role=Role.ADMIN, db=db, lake=lake)
    body = client.post("/api/v1/quality/drift/alerts").json()
    assert body["status"] == "baseline_created"
    assert body["columns"] >= 1  # severity + text(非空类别列)
    # 第二次同数据 → compared,无漂移
    body2 = client.post("/api/v1/quality/drift/alerts").json()
    assert body2["status"] == "compared"
    assert body2["drifted"] == []


def test_drift_detects_shift_and_sets_metric(db: SystemDB) -> None:
    from arrow_lake.core.metrics import REGISTRY

    _, lake = _ready(db)
    client = _make_app(role=Role.ADMIN, db=db, lake=lake)
    client.post("/api/v1/quality/drift/alerts")          # 落基线(50/50)
    lake._tables["alerts"] = _skewed_table()              # 数据偏移(90/10)
    body = client.post("/api/v1/quality/drift/alerts").json()
    assert body["status"] == "compared"
    assert "severity" in body["drifted"]
    assert body["columns"]["severity"]["kl"] > 0.1
    # metrics Gauge 已写
    val = REGISTRY.get_sample_value(
        "arrow_lake_quality_drift_kl", {"dataset": "alerts", "column": "severity"})
    assert val is not None and val > 0.1


def test_drift_reset_rebaselines(db: SystemDB) -> None:
    _, lake = _ready(db)
    client = _make_app(role=Role.ADMIN, db=db, lake=lake)
    client.post("/api/v1/quality/drift/alerts")
    lake._tables["alerts"] = _skewed_table()
    body = client.post("/api/v1/quality/drift/alerts?reset=true").json()
    assert body["status"] == "baseline_reset"
    # 重置后同数据无漂移
    assert client.post("/api/v1/quality/drift/alerts").json()["drifted"] == []


def test_drift_unknown_dataset_404_and_threshold_override(db: SystemDB) -> None:
    lake = FakeLake({})
    client = _make_app(role=Role.ADMIN, db=db, lake=lake)
    assert client.post("/api/v1/quality/drift/ghost").status_code == 404
    # 契约 drift_kl 覆盖 → threshold 回带(_ready 之后再存,避免被版本链盖掉)
    _, lake2 = _ready(db)
    ContractStore(db).save_contract("alerts", CONTRACT + "quality:\n  drift_kl: 0.5\n")
    client2 = _make_app(role=Role.ADMIN, db=db, lake=lake2)
    client2.post("/api/v1/quality/drift/alerts")
    lake2._tables["alerts"] = _skewed_table()
    body = client2.post("/api/v1/quality/drift/alerts").json()
    assert body["threshold"] == 0.5
    assert body["drifted"] == []  # 同样的偏移在 0.5 阈下不漂


# === W2 relevance(POST /quality/relevance/{ds}) =============================

class _StaticProvider:
    """relevance 端点注入的静态 LLM(测试/运维钩子)。"""

    model = "test-model"

    def __init__(self, content: str) -> None:
        self._content = content

    async def generate(self, messages):
        from arrow_lake.rag.provider import LLMResponse

        return LLMResponse(
            content=self._content, model=self.model, provider="fake")


def test_relevance_endpoint_llm_only_accepted(db: SystemDB) -> None:
    _, lake = _ready(db)
    client = _make_app(
        role=Role.ADMIN, db=db, lake=lake,
        relevance_provider=_StaticProvider("高相关"))
    r = client.post("/api/v1/quality/relevance/alerts?n=4")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["project"] == "alerts__relevance"
    assert body["mode"].startswith("llm_only")  # LS 未配置 → 自动降级
    assert body["sampled"] == 4
    # 项目已注册(回收 scheduler 可自动拾取 active 项目)
    from arrow_lake.system_db.stores.annotation import AnnotationProjectStore

    rec = AnnotationProjectStore(db).get_project("alerts__relevance")
    assert rec is not None and rec["status"] == "active"
    assert "高相关" in rec["labeling_config"]


def test_relevance_endpoint_errors(db: SystemDB) -> None:
    lake = FakeLake({})
    client = _make_app(role=Role.ADMIN, db=db, lake=lake)
    assert client.post("/api/v1/quality/relevance/ghost").status_code == 404
    # 无 LS 无 LLM → 503
    _, lake2 = _ready(db)
    client2 = _make_app(role=Role.ADMIN, db=db, lake=lake2)
    assert client2.post("/api/v1/quality/relevance/alerts").status_code == 503
    # 文本列缺失 → 422
    no_text = FakeLake({"alerts": pa.table({"x": [1, 2]})})
    client3 = _make_app(
        role=Role.ADMIN, db=db, lake=no_text,
        relevance_provider=_StaticProvider("高相关"))
    assert client3.post(
        "/api/v1/quality/relevance/alerts").status_code == 422


def test_relevance_endpoint_non_admin_403(db: SystemDB) -> None:
    _, lake = _ready(db)
    client = _make_app(
        role=Role.VIEWER, db=db, lake=lake,
        relevance_provider=_StaticProvider("高相关"))
    assert client.post("/api/v1/quality/relevance/alerts").status_code == 403


def test_assess_relevance_dimension_from_adl(db: SystemDB) -> None:
    """W2.g:relevance 行进 ADL → assess 五维全评估,verdict=pass。"""
    from arrow_lake.annotation.adl import ADL_SCHEMA

    l4 = _adl()  # L4 双标注(κ=1)
    rel_rows = []
    for i in range(4):
        rel_rows.append({
            "adl_id": f"rel{i}-ann1", "source_dataset": "alerts",
            "source_row_id": f"rel-r{i}", "objects": [], "events": [],
            "rules_applied": [], "scenario": "高相关", "relations": [],
            "annotator_id": "ann1", "annotated_at": NOW.isoformat(),
            "review_status": "approved", "reviewer_id": "", "batch_id": "b",
            "adl_version": 1,
        })
    adl = pa.concat_tables([l4, pa.Table.from_pylist(rel_rows, schema=ADL_SCHEMA)])
    ContractStore(db).save_contract("alerts", CONTRACT)
    lake = FakeLake({"alerts": _source_table(), "alerts_adl": adl})
    client = _make_app(role=Role.ADMIN, db=db, lake=lake)
    body = client.post("/api/v1/quality/assess/alerts").json()
    assert body["dimensions"]["relevance"]["score"] == 100.0
    assert body["dimensions"]["relevance"]["source"] == "annotation"
    assert body["degraded"] == []
    assert body["verdict"] == "pass"
    # 五维全评:20+35+20+7.5+10 = 92.5
    assert body["total_score"] == 92.5


# === W4.3 飞轮(POST /quality/feedback/{ds}) ==================================

class _FakeLSFeedback:
    def __init__(self) -> None:
        self.imported: list[dict] = []

    def export_tasks(self, pid: int) -> list[dict]:
        return [{"data": {"row_id": t["data"]["row_id"],
                          "strategy": t["data"]["strategy"],
                          "text": t["data"]["text"]}}
                for t in self.imported]

    def import_tasks(self, pid: int, tasks: list[dict]) -> dict:
        self.imported.extend(tasks)
        return {"task_ids": list(range(len(tasks)))}


def _feedback_app(db, lake, fake_ls):
    import types

    from arrow_lake.system_db.stores.annotation import AnnotationProjectStore

    client = _make_app(role=Role.ADMIN, db=db, lake=lake)
    client.app.state.config = types.SimpleNamespace(
        annotation=types.SimpleNamespace(
            ls_url="http://ls", ls_api_token="tok"))
    client.app.state.annotation_ls_client_factory = (
        lambda url, token: fake_ls)
    return client


def test_feedback_queues_and_audits(db: SystemDB) -> None:
    from arrow_lake.annotation.dispatch import stable_row_id
    from arrow_lake.system_db.stores.annotation import AnnotationProjectStore

    text = "管道压力异常待研判"
    lake = FakeLake({"alerts": pa.table({"text": pa.array([text], pa.string())})})
    store = AnnotationProjectStore(db)
    store.create_project(
        name="alerts_l4", dataset="alerts", template_name="alert_l4",
        labeling_config="<View/>", config_source="generated")
    store.set_ls_project_id("alerts_l4", 11)
    fake = _FakeLSFeedback()
    client = _feedback_app(db, lake, fake)
    rid = stable_row_id(text, 0)
    body = client.post("/api/v1/quality/feedback/alerts",
                       json={"object_rows": [rid]}).json()
    assert body["queued"] == 1 and body["project"] == "alerts_l4"
    assert fake.imported[0]["data"]["strategy"] == "feedback"
    assert fake.imported[0]["data"]["row_id"] == rid
    assert any(a[0] == "quality.feedback" for a in lake.audit_calls)
    # 幂等:重跑同行 → already_queued
    body2 = client.post("/api/v1/quality/feedback/alerts",
                        json={"object_rows": [rid]}).json()
    assert body2["queued"] == 0 and body2["already_queued"] == 1


def test_feedback_missing_rows_reported(db: SystemDB) -> None:
    from arrow_lake.system_db.stores.annotation import AnnotationProjectStore

    lake = FakeLake({"alerts": pa.table({"text": pa.array(["x"], pa.string())})})
    store = AnnotationProjectStore(db)
    store.create_project(
        name="alerts_l4", dataset="alerts", template_name="t",
        labeling_config="<View/>", config_source="generated")
    store.set_ls_project_id("alerts_l4", 11)
    client = _feedback_app(db, lake, _FakeLSFeedback())
    body = client.post("/api/v1/quality/feedback/alerts",
                       json={"object_rows": ["ghost-row"]}).json()
    assert body["queued"] == 0 and body["missing_rows"] == ["ghost-row"]


def test_feedback_no_l4_project_422(db: SystemDB) -> None:
    from arrow_lake.system_db.stores.annotation import AnnotationProjectStore

    lake = FakeLake({"alerts": pa.table({"text": pa.array(["x"], pa.string())})})
    AnnotationProjectStore(db).create_project(
        name="alerts__relevance", dataset="alerts", template_name="relevance",
        labeling_config="<View/>", config_source="generated")
    client = _feedback_app(db, lake, _FakeLSFeedback())
    r = client.post("/api/v1/quality/feedback/alerts",
                    json={"object_rows": ["h0"]})
    assert r.status_code == 422 and "L4 project" in r.json()["detail"]


def test_feedback_non_admin_403(db: SystemDB) -> None:
    import types

    lake = FakeLake({"alerts": pa.table({"text": pa.array(["x"], pa.string())})})
    client = _make_app(role=Role.VIEWER, db=db, lake=lake)
    client.app.state.config = types.SimpleNamespace(
        annotation=types.SimpleNamespace(ls_url="http://ls", ls_api_token="t"))
    assert client.post(
        "/api/v1/quality/feedback/alerts", json={"object_rows": ["h0"]}
    ).status_code == 403
