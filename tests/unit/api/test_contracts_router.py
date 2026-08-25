"""W4.1 — /api/v1/contracts admin API(列表/版本链/diff/保存)。

契约(实施计划 §4 W4.1):
* 全部 ADMIN(非 admin → 403);
* system_db 关闭(store 缺失)→ 503 降级;
* PUT 先 parse 校验(422),dataset 字段须与 scope 一致(422);
* 同 hash 保存不新增版本;变更 → 新版本 + 结构化 diff。
"""

from __future__ import annotations

import pytest
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.contracts import ContractStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

V1 = """
dataset: gas_net
tables:
  segments:
    columns:
      - name: material
        enum: [PE, steel]
"""

V2 = """
dataset: gas_net
tables:
  segments:
    columns:
      - name: material
        enum: [PE, steel, ductile_iron]
"""


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


def _make_app(*, role: Role, db: SystemDB | None) -> TestClient:
    from arrow_lake.api.routers.contracts import router

    app = FastAPI()
    app.state.contract_store = ContractStore(db) if db else None

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(sub="tester", role=role, exp=0, iat=0)
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


# --- access control ---------------------------------------------------------

def test_non_admin_403(db: SystemDB) -> None:
    client = _make_app(role=Role.VIEWER, db=db)
    for path in ("/api/v1/contracts", "/api/v1/contracts/gas_net",
                 "/api/v1/contracts/gas_net/versions"):
        assert client.get(path).status_code == 403, path
    assert client.put(
        "/api/v1/contracts/gas_net", json={"contract_yaml": V1}
    ).status_code == 403


def test_store_missing_503() -> None:
    client = _make_app(role=Role.ADMIN, db=None)
    assert client.get("/api/v1/contracts").status_code == 503
    assert client.get("/api/v1/contracts/gas_net").status_code == 503


# --- save / read flows ------------------------------------------------------

def test_save_and_get_latest(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    r = client.put("/api/v1/contracts/gas_net", json={"contract_yaml": V1})
    assert r.status_code == 200
    assert r.json()["version"] == 1 and r.json()["created"] is True

    got = client.get("/api/v1/contracts/gas_net")
    assert got.status_code == 200
    assert "enum" in got.json()["contract_yaml"]


def test_same_hash_save_skips_version(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    client.put("/api/v1/contracts/gas_net", json={"contract_yaml": V1})
    r = client.put("/api/v1/contracts/gas_net", json={"contract_yaml": V1})
    assert r.json()["created"] is False and r.json()["version"] == 1
    assert len(client.get("/api/v1/contracts/gas_net/versions").json()["versions"]) == 1


def test_change_creates_v2_with_diff(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    client.put("/api/v1/contracts/gas_net", json={"contract_yaml": V1})
    r = client.put("/api/v1/contracts/gas_net", json={"contract_yaml": V2})
    assert r.json()["version"] == 2 and r.json()["created"] is True

    diff = client.get("/api/v1/contracts/gas_net/diff").json()["diff"]
    seg_cols = diff["tables"]["segments"]["columns"]
    assert any(c["column"] == "material" and c["change"] == "changed" for c in seg_cols)

    versions = client.get("/api/v1/contracts/gas_net/versions").json()["versions"]
    assert [v["version"] for v in versions] == [2, 1]
    v1 = client.get("/api/v1/contracts/gas_net/versions/1").json()
    assert "ductile_iron" not in v1["contract_yaml"]


def test_list_scopes(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    client.put("/api/v1/contracts/gas_net", json={"contract_yaml": V1})
    client.put("/api/v1/contracts/other", json={
        "contract_yaml": V1.replace("dataset: gas_net", "dataset: other")})
    scopes = client.get("/api/v1/contracts").json()
    assert scopes["total"] == 2
    assert {c["scope"] for c in scopes["contracts"]} == {"gas_net", "other"}


def test_missing_scope_404(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    assert client.get("/api/v1/contracts/ghost").status_code == 404
    assert client.get("/api/v1/contracts/ghost/versions/1").status_code == 404


def test_invalid_yaml_422(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    r = client.put("/api/v1/contracts/gas_net", json={
        "contract_yaml": "dataset: gas_net\nontology:\n  columns:\n    - name: x\n      enum: []\n"})
    assert r.status_code == 422


def test_scope_dataset_mismatch_422(db: SystemDB) -> None:
    client = _make_app(role=Role.ADMIN, db=db)
    r = client.put("/api/v1/contracts/wrong_scope", json={"contract_yaml": V1})
    assert r.status_code == 422
