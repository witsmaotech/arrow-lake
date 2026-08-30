"""W3.2 — /api/v1/release 发布门四端点(v1.11.4 MS5 F5.4)。

校验链契约(设计 §6):无报告拒 / 否决拒 / below_bronze 拒 / 拒绝劣化
(基准 = 最新 active)/ 漂移超限拒;force+reason 覆盖(audit
``release.forced``);成功 = 锁版本+语义化 tag+datasheet+基线刷新
(source=release)+ audit/lineage ``release.published``。
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pytest
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.api.deps import get_lake
from arrow_lake.api.routers.release import router
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.contracts import ContractStore
from arrow_lake.system_db.stores.drift_baselines import DriftBaselineStore
from arrow_lake.system_db.stores.quality_reports import QualityReportStore
from arrow_lake.system_db.stores.releases import ReleaseStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


class FakeRelLake:
    """发布面最小 Lake:read/open(version)/audit/lineage。"""

    def __init__(self, tables: dict[str, pa.Table],
                 versions: dict[str, int] | None = None) -> None:
        self._tables = tables
        self._versions = versions or {}
        self.audit_calls: list[tuple[str, dict]] = []
        self.lineage_calls: list[tuple[str, str, dict]] = []

    def read_dataset(self, name: str, columns=None, table=None):
        if name not in self._tables:
            raise KeyError(name)
        return self._tables[name]

    def _get_storage(self) -> FakeRelLake:
        return self

    def open_dataset(self, name: str, table=None) -> Any:
        import types

        return types.SimpleNamespace(version=self._versions.get(name, 5))

    def audit_record(self, event_type: str, **kw) -> str:
        self.audit_calls.append((event_type, kw))
        return "audit-1"

    def lineage_record_event(self, dataset: str, operation: str, **kw) -> None:
        self.lineage_calls.append((dataset, operation, kw))


_TABLE = pa.table({
    "severity": pa.array(["high", "low", "high"]),
    "text": pa.array(["a", "b", "c"], pa.string()),
})


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


def _make_app(role: Role, db: SystemDB | None,
              lake: FakeRelLake | None) -> TestClient:
    app = FastAPI()
    if db is not None:
        app.state.release_store = ReleaseStore(db)
        app.state.quality_report_store = QualityReportStore(db)
        app.state.drift_baseline_store = DriftBaselineStore(db)
        app.state.contract_store = ContractStore(db)

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(sub="tester", role=role, exp=0, iat=0)
        return await call_next(request)

    if lake is not None:
        app.dependency_overrides[get_lake] = lambda: lake
    app.include_router(router)
    return TestClient(app)


def _report(db: SystemDB, *, total: float = 92.5, admission: str = "silver",
            verdict: str = "pass", vetoes: list | None = None) -> None:
    QualityReportStore(db).create_report(
        "alerts", total_score=total, star=4, admission=admission,
        verdict=verdict,
        dimensions={"accuracy": {"score": total, "details": {"kappa": 0.9},
                                 "source": "adl"}},
        vetoes=vetoes or [], degraded=[], spec={}, assessed_by="tester")


def _ready(db: SystemDB, **kw) -> FakeRelLake:
    _report(db, **kw)
    return FakeRelLake({"alerts": _TABLE}, versions={"alerts": 7})


# --- 访问控制 / 降级 ---------------------------------------------------------

def test_non_admin_403_and_store_missing_503(db: SystemDB) -> None:
    lake = _ready(db)
    client = _make_app(Role.VIEWER, db, lake)
    assert client.post(
        "/api/v1/release/alerts", json={"changelog": "x"}).status_code == 403
    assert _make_app(Role.ADMIN, None, lake).post(
        "/api/v1/release/alerts", json={"changelog": "x"}).status_code == 503


# --- 发布主链 ---------------------------------------------------------------

def test_publish_happy_path_locks_version_and_audits(db: SystemDB) -> None:
    lake = _ready(db)
    client = _make_app(Role.ADMIN, db, lake)
    r = client.post("/api/v1/release/alerts",
                    json={"changelog": "首个发布", "category": "project"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tag"] == "v1.0.0" and body["lance_version"] == 7
    assert body["forced"] is False and body["status"] == "active"
    assert body["datasheet"]["id"] == "alerts"
    assert body["datasheet"]["quality"]["kappa"] == 0.9
    # audit + lineage 双落痕
    assert lake.audit_calls[0][0] == "release.published"
    assert lake.lineage_calls[0][:2] == ("alerts", "release.published")
    meta = lake.lineage_calls[0][2]
    assert meta["metadata"]["tag"] == "v1.0.0"
    # 发布时基线自动快照(source=release)
    base = DriftBaselineStore(db).get_baseline("alerts")
    assert base is not None and base["source"] == "release"


def test_no_report_422(db: SystemDB) -> None:
    lake = FakeRelLake({"alerts": _TABLE})
    client = _make_app(Role.ADMIN, db, lake)
    r = client.post("/api/v1/release/alerts", json={"changelog": "x"})
    assert r.status_code == 422
    assert "assess" in r.json()["detail"]


def test_veto_blocked_and_force_requires_reason(db: SystemDB) -> None:
    lake = _ready(db, total=90.0, verdict="veto",
                  vetoes=[{"kind": "accuracy_below_threshold",
                           "dimension": "accuracy", "score": 60.0,
                           "threshold": 81.0}])
    client = _make_app(Role.ADMIN, db, lake)
    r = client.post("/api/v1/release/alerts", json={"changelog": "x"})
    assert r.status_code == 422
    assert any(b.startswith("veto:") for b in r.json()["detail"]["blocked"])
    # force 无 reason → 422;有 reason → 过(audit release.forced)
    r2 = client.post("/api/v1/release/alerts",
                     json={"changelog": "x", "force": True})
    assert r2.status_code == 422
    r3 = client.post("/api/v1/release/alerts",
                     json={"changelog": "x", "force": True, "reason": "试点豁免"})
    assert r3.status_code == 200 and r3.json()["forced"] is True
    assert r3.json()["overridden_reasons"]
    kinds = [a[0] for a in lake.audit_calls]
    assert "release.forced" in kinds and "release.published" in kinds


def test_below_bronze_blocked(db: SystemDB) -> None:
    lake = _ready(db, total=70.0, admission="none")
    client = _make_app(Role.ADMIN, db, lake)
    r = client.post("/api/v1/release/alerts", json={"changelog": "x"})
    assert r.status_code == 422
    assert "below_bronze" in r.json()["detail"]["blocked"]


def test_regression_blocked_vs_latest_active(db: SystemDB) -> None:
    lake = _ready(db, total=95.0, admission="silver")
    client = _make_app(Role.ADMIN, db, lake)
    assert client.post(
        "/api/v1/release/alerts", json={"changelog": "v1"}).status_code == 200
    # 新报告降分 → 劣化拒
    _report(db, total=88.0, admission="bronze")
    r = client.post("/api/v1/release/alerts", json={"changelog": "v2"})
    assert r.status_code == 422
    assert "regression" in r.json()["detail"]["blocked"]
    # 退役高版本后,劣化基准回落 → 可发
    ReleaseStore(db).retire_release("alerts", "v1.0.0")
    assert client.post(
        "/api/v1/release/alerts", json={"changelog": "v2 again"}
    ).status_code == 200


def test_drift_exceeded_blocked(db: SystemDB) -> None:
    lake = _ready(db)
    client = _make_app(Role.ADMIN, db, lake)
    # 落基线(50/50 均匀),再换 90/10 偏移表
    DriftBaselineStore(db).set_baseline("alerts", {
        "severity": {"kind": "categorical",
                     "values": {"high": 2, "low": 1}, "other": 0, "total": 3},
    })
    skewed = pa.table({
        "severity": pa.array(["high"] * 29 + ["low"]),
        "text": pa.array([f"t{i}" for i in range(30)], pa.string()),
    })
    lake._tables["alerts"] = skewed
    r = client.post("/api/v1/release/alerts", json={"changelog": "x"})
    assert r.status_code == 422
    assert any(b.startswith("drift:") for b in r.json()["detail"]["blocked"])


def test_bump_semantics(db: SystemDB) -> None:
    lake = _ready(db)
    client = _make_app(Role.ADMIN, db, lake)
    client.post("/api/v1/release/alerts", json={"changelog": "1"})
    t2 = client.post("/api/v1/release/alerts",
                     json={"changelog": "2"}).json()["tag"]
    t3 = client.post("/api/v1/release/alerts",
                     json={"changelog": "3", "bump": "major"}
                     ).json()["tag"]
    t4 = client.post("/api/v1/release/alerts",
                     json={"changelog": "4", "bump": "patch"}
                     ).json()["tag"]
    assert [t2, t3, t4] == ["v1.1.0", "v2.0.0", "v2.0.1"]  # 默认 minor


# --- 历史 / retire / datasheet ----------------------------------------------

def test_history_retire_datasheet(db: SystemDB) -> None:
    lake = _ready(db)
    client = _make_app(Role.ADMIN, db, lake)
    client.post("/api/v1/release/alerts", json={"changelog": "一版"})
    client.post("/api/v1/release/alerts",
                json={"changelog": "二版", "bump": "major"})
    hist = client.get("/api/v1/release/alerts").json()
    assert [r["tag"] for r in hist["releases"]] == ["v2.0.0", "v1.0.0"]
    assert hist["latest_active"]["tag"] == "v2.0.0"
    # datasheet:默认最新 active;?tag= 指定
    y1 = client.get("/api/v1/release/alerts/datasheet")
    assert y1.status_code == 200 and "version: v2.0.0" in y1.text
    y2 = client.get("/api/v1/release/alerts/datasheet?tag=v1.0.0")
    assert "version: v1.0.0" in y2.text and "changelog: 一版" in y2.text
    # retire → 404 再退;latest active 回落
    assert client.post("/api/v1/release/alerts/retire",
                       json={"tag": "v2.0.0"}).json()["status"] == "retired"
    assert client.post("/api/v1/release/alerts/retire",
                       json={"tag": "v2.0.0"}).status_code == 404
    assert client.get("/api/v1/release/alerts").json()[
        "latest_active"]["tag"] == "v1.0.0"
    assert lake.audit_calls[-1][0] == "release.retired"


def test_datasheet_404_when_none(db: SystemDB) -> None:
    lake = FakeRelLake({"alerts": _TABLE})
    client = _make_app(Role.ADMIN, db, lake)
    assert client.get("/api/v1/release/alerts/datasheet").status_code == 404
    assert client.get("/api/v1/release/ghost").json()["total"] == 0
