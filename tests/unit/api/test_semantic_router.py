"""W3.3 — /api/v1/semantic(对齐配置 CRUD + 单位注册表只读)。

契约(实施计划 W3.3):配置面 ADMIN(非 admin → 403);system_db 关闭 → 503;
保存=解析先行的 422;dataset 与 scope 一致(沿 contracts 惯例);保存触发
lineage 事件(semantic_alignment);lineage 失败不阻塞保存(lineage_recorded=false)。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.semantic_alignments import SemanticAlignmentStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ALIGN_YAML = """
dataset: gas_net
tables:
  measurements_src_b:
    columns:
      压力: {unit: {from: MPa, to: kPa}}
"""


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


def _client(db: SystemDB | None, *, role: Role = Role.ADMIN,
            lake=None) -> TestClient:
    from arrow_lake.api.routers.semantic import router

    app = FastAPI()
    app.state.semantic_alignment_store = SemanticAlignmentStore(db) if db else None
    app.state.contract_store = None
    app.state.lake = lake

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(sub="tester", role=role, exp=0, iat=0)
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


class TestAlignmentCRUD:
    def test_put_then_get(self, db: SystemDB) -> None:
        with _client(db) as c:
            r = c.put("/api/v1/semantic/alignments/gas_net",
                      json={"alignment_yaml": ALIGN_YAML})
            assert r.status_code == 200, r.text
            assert r.json()["version"] == 1
            g = c.get("/api/v1/semantic/alignments/gas_net")
        assert g.status_code == 200
        assert "from: MPa" in g.json()["alignment_yaml"]

    def test_put_invalid_yaml_422(self, db: SystemDB) -> None:
        bad = "dataset: d\ntables:\n  t:\n    columns:\n      x: {unit: {from: kPa, to: m}}\n"
        with _client(db) as c:
            r = c.put("/api/v1/semantic/alignments/d", json={"alignment_yaml": bad})
        assert r.status_code == 422

    def test_put_scope_mismatch_422(self, db: SystemDB) -> None:
        with _client(db) as c:
            r = c.put("/api/v1/semantic/alignments/other",
                      json={"alignment_yaml": ALIGN_YAML})
        assert r.status_code == 422

    def test_delete(self, db: SystemDB) -> None:
        with _client(db) as c:
            c.put("/api/v1/semantic/alignments/gas_net", json={"alignment_yaml": ALIGN_YAML})
            r = c.delete("/api/v1/semantic/alignments/gas_net")
            assert r.status_code == 200
            assert c.get("/api/v1/semantic/alignments/gas_net").status_code == 404

    def test_non_admin_403(self, db: SystemDB) -> None:
        with _client(db, role=Role.VIEWER) as c:
            assert c.put("/api/v1/semantic/alignments/gas_net",
                         json={"alignment_yaml": ALIGN_YAML}).status_code == 403
            assert c.delete("/api/v1/semantic/alignments/gas_net").status_code == 403

    def test_store_disabled_503(self) -> None:
        with _client(None) as c:
            assert c.get("/api/v1/semantic/alignments").status_code == 503


class TestLineage:
    def test_save_records_lineage_event(self, db: SystemDB) -> None:
        calls: list[tuple] = []

        def rec(*args, **kwargs):
            calls.append((args, kwargs))

        with _client(db, lake=SimpleNamespace(lineage_record_event=rec)) as c:
            r = c.put("/api/v1/semantic/alignments/gas_net",
                      json={"alignment_yaml": ALIGN_YAML})
        assert r.json()["lineage_recorded"] is True
        assert calls and calls[0][0] == ("gas_net", "semantic_alignment")
        assert calls[0][1]["metadata"]["version"] == 1

    def test_lineage_failure_does_not_block_save(self, db: SystemDB) -> None:
        def boom(*args, **kwargs):
            raise RuntimeError("lineage down")

        with _client(db, lake=SimpleNamespace(lineage_record_event=boom)) as c:
            r = c.put("/api/v1/semantic/alignments/gas_net",
                      json={"alignment_yaml": ALIGN_YAML})
        assert r.status_code == 200
        assert r.json()["lineage_recorded"] is False


class TestUnitsEndpoint:
    def test_units_listing_viewer_ok(self, db: SystemDB) -> None:
        with _client(db, role=Role.VIEWER) as c:
            r = c.get("/api/v1/semantic/units")
        assert r.status_code == 200
        dims = r.json()["data"]["dimensions"]
        assert dims["pressure"]["kPa"]["factor"] == 1000.0
        assert dims["temperature"]["°C"]["offset"] == 273.15

    def test_units_available_without_store(self) -> None:
        with _client(None, role=Role.VIEWER) as c:
            assert c.get("/api/v1/semantic/units").status_code == 200
