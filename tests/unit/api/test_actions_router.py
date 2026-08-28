"""W2.3 — /api/v1/actions 行动目录+场景 admin API。

契约(实施计划 §3 W2.3):
* 全部 ADMIN(非 admin → 403);system_db 关闭(store 缺失)→ 503;
* PUT 先 capped 解析+模型校验(422),id 字段须与路径一致(422);
* scenario 保存期引用校验:steps 引 action 必须在目录(422,issues 齐报);
* 同 hash 保存不新增版本;变更 → 新版本;/scenarios 不被 /{action_id} 捕获。
"""

from __future__ import annotations

import pytest
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.actions import ActionCatalogStore
from arrow_lake.system_db.stores.scenarios import ScenarioStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ACTION_V1 = """
action_id: GAS.ALERT.PUBLISH
title: 发布燃气泄漏预警
target: {dataset: gas_network, object_class: 告警事件}
permission: alerts:publish
preconditions:
  - "assess.confidence >= 0.8"
effect: {type: update_lifecycle, to_state: 已发布}
idempotency_key: "{{ target.object_id }}"
"""

ACTION_V2 = ACTION_V1.replace(
    "effect: {type: update_lifecycle, to_state: 已发布}",
    'effect: {type: update_lifecycle, to_state: 已发布, fields: {level: "{{ assess.level }}"}}',
)

SCENARIO_OK = """
scenario_id: GAS.LEAK.RESPONSE
title: 燃气泄漏告警响应
entries: ["target.lifecycle_state == '待研判'"]
steps:
  - id: assess
    type: assess
    rules_scope: gas_network
  - id: publish
    action: GAS.ALERT.PUBLISH
    requires: [assess]
gateways:
  - id: gate
    type: xor
    when: "assess.confidence >= 0.8"
    then: [publish]
    else: [publish]
timeout: PT30M
on_timeout: publish
"""


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


def _make_app(*, role: Role, db: SystemDB | None) -> TestClient:
    from arrow_lake.api.routers.actions import router

    app = FastAPI()
    app.state.action_store = ActionCatalogStore(db) if db else None
    app.state.scenario_store = ScenarioStore(db) if db else None

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(sub="tester", role=role, exp=0, iat=0)
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


# --- access control ---------------------------------------------------------


def test_non_admin_403(db: SystemDB) -> None:
    client = _make_app(role=Role.VIEWER, db=db)
    for path in (
        "/api/v1/actions",
        "/api/v1/actions/GAS.ALERT.PUBLISH",
        "/api/v1/actions/GAS.ALERT.PUBLISH/versions",
        "/api/v1/actions/scenarios",
    ):
        assert client.get(path).status_code == 403, path
    assert (
        client.put("/api/v1/actions/GAS.ALERT.PUBLISH", json={"action_yaml": ACTION_V1}).status_code
        == 403
    )
    assert (
        client.put(
            "/api/v1/actions/scenarios/GAS.LEAK.RESPONSE",
            json={"scenario_yaml": SCENARIO_OK},
        ).status_code
        == 403
    )


def test_store_missing_503() -> None:
    client = _make_app(role=Role.ADMIN, db=None)
    assert client.get("/api/v1/actions").status_code == 503
    assert client.get("/api/v1/actions/scenarios").status_code == 503


# --- catalog flows -----------------------------------------------------------


def test_save_and_get_latest(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    r = client.put("/api/v1/actions/GAS.ALERT.PUBLISH", json={"action_yaml": ACTION_V1})
    assert r.status_code == 200
    assert r.json()["version"] == 1 and r.json()["created"] is True

    got = client.get("/api/v1/actions/GAS.ALERT.PUBLISH")
    assert got.status_code == 200
    assert "发布燃气泄漏预警" in got.json()["action_yaml"]


def test_same_hash_save_skips_version(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    client.put("/api/v1/actions/GAS.ALERT.PUBLISH", json={"action_yaml": ACTION_V1})
    r = client.put("/api/v1/actions/GAS.ALERT.PUBLISH", json={"action_yaml": ACTION_V1})
    assert r.json()["created"] is False and r.json()["version"] == 1
    assert len(client.get("/api/v1/actions/GAS.ALERT.PUBLISH/versions").json()["versions"]) == 1


def test_change_creates_v2(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    client.put("/api/v1/actions/GAS.ALERT.PUBLISH", json={"action_yaml": ACTION_V1})
    r = client.put("/api/v1/actions/GAS.ALERT.PUBLISH", json={"action_yaml": ACTION_V2})
    assert r.json()["version"] == 2 and r.json()["created"] is True
    versions = client.get("/api/v1/actions/GAS.ALERT.PUBLISH/versions").json()["versions"]
    assert [v["version"] for v in versions] == [2, 1]
    v1 = client.get("/api/v1/actions/GAS.ALERT.PUBLISH/versions/1").json()
    assert "fields" not in v1["action_yaml"]


def test_list_and_delete(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    client.put("/api/v1/actions/GAS.ALERT.PUBLISH", json={"action_yaml": ACTION_V1})
    client.put(
        "/api/v1/actions/GAS.ALERT.ESCALATE",
        json={"action_yaml": ACTION_V1.replace("GAS.ALERT.PUBLISH", "GAS.ALERT.ESCALATE")},
    )
    listing = client.get("/api/v1/actions").json()
    assert listing["total"] == 2
    assert {a["action_id"] for a in listing["actions"]} == {
        "GAS.ALERT.PUBLISH",
        "GAS.ALERT.ESCALATE",
    }

    assert client.delete("/api/v1/actions/GAS.ALERT.ESCALATE").json()["deleted"] is True
    assert client.delete("/api/v1/actions/GAS.ALERT.ESCALATE").status_code == 404


def test_invalid_action_yaml_422(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    r = client.put(
        "/api/v1/actions/BAD.ACTION",
        json={"action_yaml": "action_id: BAD.ACTION\neffect: {type: delete_row}\ntitle: x"},
    )
    assert r.status_code == 422


def test_action_id_mismatch_422(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    r = client.put("/api/v1/actions/WRONG.ID", json={"action_yaml": ACTION_V1})
    assert r.status_code == 422


def test_missing_action_404(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    assert client.get("/api/v1/actions/NO.SUCH").status_code == 404
    assert client.get("/api/v1/actions/NO.SUCH/versions/1").status_code == 404


# --- scenario flows ----------------------------------------------------------


def test_scenario_save_requires_action_in_catalog(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    r = client.put(
        "/api/v1/actions/scenarios/GAS.LEAK.RESPONSE", json={"scenario_yaml": SCENARIO_OK}
    )
    assert r.status_code == 422
    issues = r.json()["detail"]["issues"]
    assert any("GAS.ALERT.PUBLISH" in i for i in issues)

    # action 入目录后保存成功
    client.put("/api/v1/actions/GAS.ALERT.PUBLISH", json={"action_yaml": ACTION_V1})
    r2 = client.put(
        "/api/v1/actions/scenarios/GAS.LEAK.RESPONSE", json={"scenario_yaml": SCENARIO_OK}
    )
    assert r2.status_code == 200 and r2.json()["version"] == 1

    got = client.get("/api/v1/actions/scenarios/GAS.LEAK.RESPONSE")
    assert got.status_code == 200 and "GAS.LEAK.RESPONSE" in got.json()["scenario_yaml"]


def test_scenario_routes_not_captured_by_action_id(db: SystemDB) -> None:
    # /scenarios 先注册:列表命中 scenarios 而非 action_id="scenarios"
    client = _make_app(role=Role.ADMIN, db=db)
    client.put("/api/v1/actions/GAS.ALERT.PUBLISH", json={"action_yaml": ACTION_V1})
    client.put("/api/v1/actions/scenarios/GAS.LEAK.RESPONSE", json={"scenario_yaml": SCENARIO_OK})
    listing = client.get("/api/v1/actions/scenarios").json()
    assert listing["total"] == 1
    assert listing["scenarios"][0]["scenario_id"] == "GAS.LEAK.RESPONSE"
    # /actions 列表不含 "scenarios" 伪 scope
    actions = client.get("/api/v1/actions").json()
    assert {a["action_id"] for a in actions["actions"]} == {"GAS.ALERT.PUBLISH"}


def test_scenario_invalid_yaml_422_and_version_chain(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    client.put("/api/v1/actions/GAS.ALERT.PUBLISH", json={"action_yaml": ACTION_V1})
    r = client.put(
        "/api/v1/actions/scenarios/BAD.SCENARIO",
        json={"scenario_yaml": "scenario_id: BAD.SCENARIO\nsteps: []"},
    )
    assert r.status_code == 422

    client.put("/api/v1/actions/scenarios/GAS.LEAK.RESPONSE", json={"scenario_yaml": SCENARIO_OK})
    again = client.put(
        "/api/v1/actions/scenarios/GAS.LEAK.RESPONSE",
        json={"scenario_yaml": SCENARIO_OK.replace("timeout: PT30M", "timeout: PT1H")},
    )
    assert again.json()["version"] == 2
    versions = client.get("/api/v1/actions/scenarios/GAS.LEAK.RESPONSE/versions").json()["versions"]
    assert [v["version"] for v in versions] == [2, 1]

    assert client.delete("/api/v1/actions/scenarios/GAS.LEAK.RESPONSE").json()["deleted"]
    assert client.get("/api/v1/actions/scenarios/GAS.LEAK.RESPONSE").status_code == 404
