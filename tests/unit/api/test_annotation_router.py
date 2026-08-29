"""W1.4 — /api/v1/annotation/projects 标注项目注册 CRUD。

契约(version-plan W1.4 / 设计 §10):
* 全部 ADMIN(非 admin → 403);system_db 关闭(store 缺失)→ 503;
* POST:template_name 必须在模板 gallery(422);manual 覆盖走良构校验(422);
  重名 → 422;成功 200 且 labeling_config 生成为 LS XML(含绑定模板枚举);
* GET 列表/详情;DELETE 404/200。
"""

from __future__ import annotations

import pytest
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.annotation import AnnotationProjectStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

# gallery 真模板(带 ontology 段;文件系统读取,无 mock)
REAL_TEMPLATE = "project_concept_graph"


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


def _make_app(*, role: Role, db: SystemDB | None) -> TestClient:
    from arrow_lake.api.routers.annotation import router

    app = FastAPI()
    app.state.annotation_project_store = AnnotationProjectStore(db) if db else None

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(sub="tester", role=role, exp=0, iat=0)
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _post(client: TestClient, **overrides) -> object:
    body = {
        "name": "gas-ner-w1",
        "dataset": "demo_ms3_alerts",
        "template_name": REAL_TEMPLATE,
    }
    body.update(overrides)
    return client.post("/api/v1/annotation/projects", json=body)


class TestAuthAndAvailability:
    def test_non_admin_403(self, db: SystemDB) -> None:
        client = _make_app(role=Role.VIEWER, db=db)
        assert client.get("/api/v1/annotation/projects").status_code == 403

    def test_store_missing_503(self) -> None:
        client = _make_app(role=Role.ADMIN, db=None)
        assert client.get("/api/v1/annotation/projects").status_code == 503


class TestCreate:
    def test_create_generates_ls_config_from_template(self, db: SystemDB) -> None:
        client = _make_app(role=Role.ADMIN, db=db)
        resp = _post(client)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "gas-ner-w1"
        assert body["dataset"] == "demo_ms3_alerts"
        assert body["template_name"] == REAL_TEMPLATE
        assert body["config_source"] == "generated"
        # 生成物是真模板的 22 类枚举子集(主体/项目 都在)
        assert 'Labels name="objects"' in body["labeling_config"]
        assert '<Label value="主体"' in body["labeling_config"]
        assert body["ls_project_id"] is None  # LS transient,W2 dispatch 才重绑

    def test_unknown_template_422(self, db: SystemDB) -> None:
        client = _make_app(role=Role.ADMIN, db=db)
        resp = _post(client, template_name="no_such_template")
        assert resp.status_code == 422
        assert "template" in resp.json()["detail"].lower()

    def test_duplicate_name_422(self, db: SystemDB) -> None:
        client = _make_app(role=Role.ADMIN, db=db)
        assert _post(client).status_code == 200
        resp = _post(client, dataset="other_ds")
        assert resp.status_code == 422

    def test_manual_override_passthrough(self, db: SystemDB) -> None:
        manual = '<View><Text name="text" value="$text"/></View>'
        client = _make_app(role=Role.ADMIN, db=db)
        resp = _post(client, labeling_config_override=manual)
        assert resp.status_code == 200
        assert resp.json()["labeling_config"] == manual
        assert resp.json()["config_source"] == "manual"

    def test_manual_override_malformed_422(self, db: SystemDB) -> None:
        client = _make_app(role=Role.ADMIN, db=db)
        resp = _post(client, labeling_config_override="<View><unclosed>")
        assert resp.status_code == 422


class TestReadDelete:
    def test_list_and_get_roundtrip(self, db: SystemDB) -> None:
        client = _make_app(role=Role.ADMIN, db=db)
        _post(client)
        listing = client.get("/api/v1/annotation/projects")
        assert listing.status_code == 200
        assert listing.json()["total"] == 1
        assert listing.json()["projects"][0]["name"] == "gas-ner-w1"

        detail = client.get("/api/v1/annotation/projects/gas-ner-w1")
        assert detail.status_code == 200
        assert detail.json()["name"] == "gas-ner-w1"

    def test_get_missing_404(self, db: SystemDB) -> None:
        client = _make_app(role=Role.ADMIN, db=db)
        assert client.get("/api/v1/annotation/projects/ghost").status_code == 404

    def test_delete_then_404(self, db: SystemDB) -> None:
        client = _make_app(role=Role.ADMIN, db=db)
        _post(client)
        assert client.delete("/api/v1/annotation/projects/gas-ner-w1").status_code == 200
        assert client.get("/api/v1/annotation/projects/gas-ner-w1").status_code == 404
        assert client.delete("/api/v1/annotation/projects/gas-ner-w1").status_code == 404
