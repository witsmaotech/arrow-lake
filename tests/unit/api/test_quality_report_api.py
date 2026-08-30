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
    """最小只读 Lake:read_dataset / audit / lineage 记调用。"""

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
) -> TestClient:
    app = FastAPI()
    app.state.quality_report_store = QualityReportStore(db) if db else None
    app.state.contract_store = ContractStore(db) if db else None

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
    assert ("quality.assess",) == (lake.audit_calls[0][0],)
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
