"""W2 #4(v1.11.5)— /datasets/{name}/classification 端点。

契约:GET VIEWER(未分级 tier=null + tiers 词表回显);PUT EDITOR(422 越档
/404 数据集不存在/503 store 缺失);变更落审计 dataset.classification_changed
(from→to);VIEWER 写 403。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.classification import DatasetClassificationStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


class _Checker:
    def check_dataset_access(self, *, role, dataset, action, permissions=None):
        return True


class _Lake:
    """catalog 只回一个 alerts;audit_record 捕获。"""

    def __init__(self) -> None:
        self.audits: list[tuple] = []

    def catalog(self):
        return SimpleNamespace(datasets=[SimpleNamespace(name="alerts")])

    def audit_record(self, event, dataset_name="", actor="system", payload=None):
        self.audits.append((event, dataset_name, actor, payload))
        return "audit-1"


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


def _make_app(db: SystemDB | None, *, role: Role, lake: _Lake) -> TestClient:
    from arrow_lake.api.errors import register_exception_handlers
    from arrow_lake.api.routers.datasets import router

    app = FastAPI()
    register_exception_handlers(app)  # CatalogError → 404 同真 app
    app.state.lake = lake
    app.state.checker = _Checker()
    if db is not None:
        app.state.dataset_classification_store = DatasetClassificationStore(db)

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(sub="op", role=role, exp=0, iat=0)
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def test_get_unclassified_returns_null_with_tiers(db: SystemDB) -> None:
    lake = _Lake()
    with _make_app(db, role=Role.VIEWER, lake=lake) as c:
        r = c.get("/api/v1/datasets/alerts/classification")
        assert r.status_code == 200
        body = r.json()
        assert body["tier"] is None and "confidential" in body["tiers"]


def test_put_roundtrip_and_audit(db: SystemDB) -> None:
    lake = _Lake()
    with _make_app(db, role=Role.EDITOR, lake=lake) as c:
        r = c.put("/api/v1/datasets/alerts/classification", json={"tier": "internal"})
        assert r.status_code == 200 and r.json()["previous_tier"] is None
        r2 = c.put(
            "/api/v1/datasets/alerts/classification",
            json={"tier": "restricted", "note": "升档"},
        )
        assert r2.json()["previous_tier"] == "internal"
        g = c.get("/api/v1/datasets/alerts/classification").json()
        assert g["tier"] == "restricted" and g["note"] == "升档"
    kinds = [a[0] for a in lake.audits]
    assert kinds.count("dataset.classification_changed") == 2
    assert lake.audits[-1][3] == {"from": "internal", "to": "restricted", "note": "升档"}


def test_put_invalid_tier_422(db: SystemDB) -> None:
    with _make_app(db, role=Role.EDITOR, lake=_Lake()) as c:
        r = c.put("/api/v1/datasets/alerts/classification", json={"tier": "secret"})
        assert r.status_code == 422


def test_put_unknown_dataset_404(db: SystemDB) -> None:
    with _make_app(db, role=Role.EDITOR, lake=_Lake()) as c:
        r = c.put("/api/v1/datasets/ghost/classification", json={"tier": "public"})
        assert r.status_code == 404


def test_viewer_cannot_write(db: SystemDB) -> None:
    with _make_app(db, role=Role.VIEWER, lake=_Lake()) as c:
        r = c.put("/api/v1/datasets/alerts/classification", json={"tier": "public"})
        assert r.status_code == 403


def test_clear_classification(db: SystemDB) -> None:
    lake = _Lake()
    with _make_app(db, role=Role.EDITOR, lake=lake) as c:
        c.put("/api/v1/datasets/alerts/classification", json={"tier": "internal"})
        r = c.delete("/api/v1/datasets/alerts/classification")
        assert r.status_code == 200 and r.json() == {
            "dataset": "alerts", "tier": None, "previous_tier": "internal",
        }
        assert c.get("/api/v1/datasets/alerts/classification").json()["tier"] is None
        # 已无分级 → 再删 404
        assert c.delete("/api/v1/datasets/alerts/classification").status_code == 404
    kinds = [a[0] for a in lake.audits]
    assert kinds.count("dataset.classification_changed") == 2
    assert lake.audits[-1][3] == {"from": "internal", "to": None, "cleared": True}


def test_store_missing_503() -> None:
    with _make_app(None, role=Role.EDITOR, lake=_Lake()) as c:
        assert c.get("/api/v1/datasets/alerts/classification").status_code == 503
