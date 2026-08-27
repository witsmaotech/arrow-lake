"""W2.3 — /api/v1/objects/entity-map(源系统 ID → 对象 ID,显式维护面)。

契约(实施计划 W2.3):全部 ADMIN(非 admin → 403);system_db 关闭 → 503;
批量 upsert 幂等;DELETE 按四段键;GET 按 scope/table 过滤。
"""

from __future__ import annotations

import pytest
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.entity_map import EntityMapStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


def _client(db: SystemDB | None, *, role: Role = Role.ADMIN) -> TestClient:
    from arrow_lake.api.routers.objects import router

    app = FastAPI()
    app.state.entity_map_store = EntityMapStore(db) if db else None

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(sub="tester", role=role, exp=0, iat=0)
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


BULK = {
    "scope": "gas_net", "table": "segments",
    "mappings": [
        {"source_system": "SCADA-A", "source_id": "S-047",
         "object_id": "GAS.SEGMENT.RG01-001-S047"},
        {"source_system": "GIS-B", "source_id": "管段047",
         "object_id": "GAS.SEGMENT.RG01-001-S047"},
    ],
}


class TestEntityMapAPI:
    def test_bulk_upsert_then_list(self, db: SystemDB) -> None:
        with _client(db) as c:
            r = c.post("/api/v1/objects/entity-map", json=BULK)
            assert r.status_code == 200, r.text
            assert r.json()["data"]["written"] == 2
            lst = c.get("/api/v1/objects/entity-map?scope=gas_net&table=segments")
        assert lst.status_code == 200
        entries = lst.json()["data"]
        assert {e["source_system"] for e in entries} == {"SCADA-A", "GIS-B"}

    def test_bulk_idempotent(self, db: SystemDB) -> None:
        with _client(db) as c:
            c.post("/api/v1/objects/entity-map", json=BULK)
            c.post("/api/v1/objects/entity-map", json=BULK)
            lst = c.get("/api/v1/objects/entity-map?scope=gas_net")
        assert len(lst.json()["data"]) == 2

    def test_delete_by_key(self, db: SystemDB) -> None:
        with _client(db) as c:
            c.post("/api/v1/objects/entity-map", json=BULK)
            r = c.delete(
                "/api/v1/objects/entity-map?scope=gas_net&table=segments"
                "&source_system=SCADA-A&source_id=S-047"
            )
        assert r.status_code == 200
        assert r.json()["data"]["deleted"] is True

    def test_non_admin_403(self, db: SystemDB) -> None:
        with _client(db, role=Role.VIEWER) as c:
            assert c.post("/api/v1/objects/entity-map", json=BULK).status_code == 403
            assert c.get("/api/v1/objects/entity-map?scope=x").status_code == 403
            assert c.delete("/api/v1/objects/entity-map?scope=x&table=y"
                            "&source_system=z&source_id=w").status_code == 403

    def test_store_disabled_503(self) -> None:
        with _client(None) as c:
            assert c.get("/api/v1/objects/entity-map?scope=x").status_code == 503

    def test_empty_mappings_422(self, db: SystemDB) -> None:
        with _client(db) as c:
            r = c.post("/api/v1/objects/entity-map", json={
                "scope": "x", "table": "t", "mappings": [],
            })
        assert r.status_code == 422
