"""W2.3 — /api/v1/ontology admin API(versions 列表/diff + rules CRUD/状态迁移)。

契约(实施计划 §4 W2.3):
* 全部 ADMIN(非 admin → 403);
* system_db 关闭(store 缺失)→ 503 降级;
* diff 端点返回结构化 diff_json;
* 非法状态迁移 → 422;规则不存在 → 404。
"""

from __future__ import annotations

import pytest
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.ontology import OntologyRulesStore, OntologyVersionStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


def _make_app(*, role: Role, db: SystemDB | None) -> TestClient:
    from arrow_lake.api.routers.ontology import router

    app = FastAPI()
    app.state.ontology_store = OntologyVersionStore(db) if db else None
    app.state.ontology_rules_store = OntologyRulesStore(db) if db else None

    class _AuditLake:
        """audit_write best-effort 吞异常——替身只为通过 get_lake 依赖。"""

        def audit_record(self, *a, **kw) -> str:
            return "audit-test"

    app.state.lake = _AuditLake()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(
            sub="tester", role=role, exp=0, iat=0,
        )
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _seed_version(db: SystemDB) -> int:
    """插一条最小版本记录,返回其 id。"""
    db.execute(
        "INSERT INTO ontology_versions (scope, template_name, version, "
        " shapes_turtle, source_hash, diff_json) VALUES (?, ?, ?, ?, ?, ?)",
        ("ds_x", "t1", 1, "@prefix … EntityShape …", "hash-a", None),
    )
    db.commit()
    rows = db.execute(
        "SELECT id FROM ontology_versions WHERE scope = 'ds_x'"
    ).fetchall()
    return int(rows[0][0])


# --- access control ---------------------------------------------------------


def test_versions_denied_for_viewer(db: SystemDB) -> None:
    client = _make_app(role=Role.VIEWER, db=db)
    assert client.get("/api/v1/ontology/versions").status_code == 403


def test_rules_denied_for_editor(db: SystemDB) -> None:
    client = _make_app(role=Role.EDITOR, db=db)
    assert client.get("/api/v1/ontology/rules").status_code == 403


def test_versions_503_when_system_db_off() -> None:
    client = _make_app(role=Role.ADMIN, db=None)
    assert client.get("/api/v1/ontology/versions").status_code == 503


# --- versions ---------------------------------------------------------------


def test_versions_chain_for_scope(db: SystemDB) -> None:
    _seed_version(db)
    client = _make_app(role=Role.ADMIN, db=db)
    resp = client.get("/api/v1/ontology/versions", params={"scope": "ds_x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["count"] == 1
    row = body["data"][0]
    assert row["scope"] == "ds_x"
    assert row["version"] == 1
    assert "shapes_turtle" not in row  # 列表不带全量 Turtle


def test_version_detail_includes_turtle(db: SystemDB) -> None:
    vid = _seed_version(db)
    client = _make_app(role=Role.ADMIN, db=db)
    resp = client.get(f"/api/v1/ontology/versions/{vid}")
    assert resp.status_code == 200
    assert "EntityShape" in resp.json()["data"]["shapes_turtle"]


def test_version_detail_missing_404(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    assert client.get("/api/v1/ontology/versions/424242").status_code == 404


def test_version_diff_first_version_is_null(db: SystemDB) -> None:
    vid = _seed_version(db)
    client = _make_app(role=Role.ADMIN, db=db)
    resp = client.get(f"/api/v1/ontology/versions/{vid}/diff")
    assert resp.status_code == 200
    assert resp.json()["data"] is None  # 首版无 diff


def test_version_diff_structure(db: SystemDB) -> None:
    """diff_json 解析后必须含 增/删 两键的结构(plan TDD: diff 结构断言)。"""
    import json

    diff = {
        "entity_type_enum": {"added": ["device"], "removed": []},
        "relation_type_enum": {"added": [], "removed": []},
        "required_entity_fields": {"added": [], "removed": []},
        "required_relation_fields": {"added": [], "removed": []},
        "type_pairs": {"added": [], "removed": []},
    }
    db.execute(
        "INSERT INTO ontology_versions (scope, template_name, version, "
        " shapes_turtle, source_hash, diff_json) VALUES (?, ?, ?, ?, ?, ?)",
        ("ds_x", "t1", 2, "turtle-v2", "hash-b", json.dumps(diff)),
    )
    db.commit()
    vid = db.execute(
        "SELECT id FROM ontology_versions WHERE version = 2"
    ).fetchall()[0][0]

    client = _make_app(role=Role.ADMIN, db=db)
    resp = client.get(f"/api/v1/ontology/versions/{vid}/diff")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["entity_type_enum"]["added"] == ["device"]
    for section in data.values():
        assert set(section.keys()) == {"added", "removed"}


# --- rules CRUD + 状态机 -----------------------------------------------------


def _rule_body() -> dict:
    return {
        "rule_id": "GAS.LEAK.R001",
        "scope": "gas_pilot",
        "condition_expr": "浓度 > 20%LEL AND 持续 > 5min",
        "conclusion": "触发泄漏预警",
        "source_ref": "GB/T 50493-2019 §5.2",
    }


def test_rules_crud_roundtrip(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    resp = client.post("/api/v1/ontology/rules", json=_rule_body())
    assert resp.status_code == 201

    listed = client.get("/api/v1/ontology/rules", params={"scope": "gas_pilot"})
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["data"][0]["rule_id"] == "GAS.LEAK.R001"

    deleted = client.delete("/api/v1/ontology/rules/GAS.LEAK.R001")
    assert deleted.status_code == 200
    assert client.get("/api/v1/ontology/rules").json()["count"] == 0


def test_rules_transition_legal_and_illegal(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    client.post("/api/v1/ontology/rules", json=_rule_body())

    ok = client.post(
        "/api/v1/ontology/rules/GAS.LEAK.R001/transition",
        params={"to_status": "active"},
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["status"] == "active"

    # active → retired 合法
    assert client.post(
        "/api/v1/ontology/rules/GAS.LEAK.R001/transition",
        params={"to_status": "retired"},
    ).status_code == 200

    # retired → active 跳级非法 → 422
    illegal = client.post(
        "/api/v1/ontology/rules/GAS.LEAK.R001/transition",
        params={"to_status": "active"},
    )
    assert illegal.status_code == 422


def test_rules_transition_missing_404(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    resp = client.post(
        "/api/v1/ontology/rules/NOPE/transition", params={"to_status": "active"},
    )
    assert resp.status_code == 404


def test_rules_delete_missing_404(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    assert client.delete("/api/v1/ontology/rules/NOPE").status_code == 404


def test_rules_503_when_system_db_off() -> None:
    client = _make_app(role=Role.ADMIN, db=None)
    assert client.get("/api/v1/ontology/rules").status_code == 503
