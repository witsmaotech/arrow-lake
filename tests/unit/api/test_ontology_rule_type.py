"""W1.4 API 面 — /ontology/rules 的 rule_type 五分类 + version(v1.11.1/DR15 D-2)。

契约:POST 带 rule_type/version 透传落库;非法枚举 → 422;省略 → 插入回落
默认(validation/'1')、更新保留现值;GET ?rule_type= 过滤。
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


def _client(db: SystemDB) -> TestClient:
    from arrow_lake.api.routers.ontology import router

    app = FastAPI()
    app.state.ontology_store = OntologyVersionStore(db)
    app.state.ontology_rules_store = OntologyRulesStore(db)

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(sub="tester", role=Role.ADMIN, exp=0, iat=0)
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _post_rule(c: TestClient, **overrides) -> dict:
    body = {
        "rule_id": "GAS.LEAK.R001", "scope": "gas_pilot",
        "condition_expr": "浓度 > 20%LEL", "conclusion": "泄漏预警",
        "source_ref": "GB/T 50493-2019",
    }
    body.update(overrides)
    r = c.post("/api/v1/ontology/rules", json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]


class TestRuleTypeAPI:
    def test_post_with_type_and_version(self, db: SystemDB) -> None:
        with _client(db) as c:
            rule = _post_rule(c, rule_type="risk_control", version="1.2")
        assert rule["rule_type"] == "risk_control"
        assert rule["version"] == "1.2"

    def test_post_defaults(self, db: SystemDB) -> None:
        with _client(db) as c:
            rule = _post_rule(c)
        assert rule["rule_type"] == "validation"
        assert rule["version"] == "1"

    def test_invalid_type_422(self, db: SystemDB) -> None:
        with _client(db) as c:
            r = c.post("/api/v1/ontology/rules", json={
                "rule_id": "BAD", "scope": "*", "condition_expr": "c",
                "conclusion": "k", "source_ref": "s", "rule_type": "judgement",
            })
        assert r.status_code == 422

    def test_update_keeps_type_version_when_omitted(self, db: SystemDB) -> None:
        with _client(db) as c:
            _post_rule(c, rule_type="computation", version="7")
            rule = _post_rule(c, condition_expr="新条件")
        assert rule["condition_expr"] == "新条件"
        assert rule["rule_type"] == "computation"
        assert rule["version"] == "7"

    def test_list_filter_by_rule_type(self, db: SystemDB) -> None:
        with _client(db) as c:
            _post_rule(c, rule_id="A.V", rule_type="validation")
            _post_rule(c, rule_id="A.T", rule_type="transformation")
            r = c.get("/api/v1/ontology/rules?rule_type=transformation")
        assert r.status_code == 200
        ids = [x["rule_id"] for x in r.json()["data"]]
        assert ids == ["A.T"]
