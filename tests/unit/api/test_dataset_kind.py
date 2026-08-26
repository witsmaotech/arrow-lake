"""W4.2 后端前置 —— catalog 容器可见性 + kind 分型 + schema 二段名(DR14)。

覆盖:
1. storage.list_containers(local):容器枚举,裸表/空目录/无表目录不误报;
2. lake.catalog():容器 entry(kind=container,num_rows=表求和)+ document
   启发式(文档摄入特征列)+ 普通表格集 structured;
3. GET /datasets/{name}:容器详情带 tables 清单,kind 透传;
4. GET /datasets/{name}/schema?table=:表级 schema;容器裸名 → 422
   (D6 语义,沿 OLAP 同款错误码)。
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.config import ArrowLakeConfig, StorageBackend
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from arrow_lake import Lake
from arrow_lake.ingest.storage import LanceStorageManager


def _lake(tmp_path: Path) -> Lake:
    cfg = ArrowLakeConfig()
    cfg.storage.backend = StorageBackend.LOCAL
    return Lake(base_uri=str(tmp_path / "lake"), config=cfg)


def _client(lake: Lake, *, role: Role = Role.ADMIN) -> TestClient:
    from arrow_lake.api.errors import register_exception_handlers
    from arrow_lake.api.routers.datasets import router

    app = FastAPI()
    app.state.lake = lake
    register_exception_handlers(app)

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(sub="tester", role=role, exp=0, iat=0)
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def populated(tmp_path: Path) -> Lake:
    lake = _lake(tmp_path)
    st = lake._get_storage()
    # 容器:2 表(3+2 行)
    st.create_dataset("gas_net", pa.table({"a": [1, 2, 3]}), table="segments")
    st.create_dataset("gas_net", pa.table({"a": [10, 20]}), table="stations")
    # 文档集:文档摄入特征列
    st.create_dataset("papers", pa.table({
        "chunk_text": ["x"], "page_number": [1], "title": ["t"],
    }))
    # 普通表格集
    st.create_dataset("sales", pa.table({"amount": [1.0], "city": ["sz"]}))
    return lake


# --- storage.list_containers -------------------------------------------------


def test_list_containers_local(tmp_path: Path) -> None:
    st = LanceStorageManager(str(tmp_path / "lake"))
    st.create_dataset("c1", pa.table({"a": [1]}), table="t1")
    st.create_dataset("plain", pa.table({"a": [1]}))
    (tmp_path / "lake" / "empty_dir").mkdir(parents=True)          # 无表目录
    (tmp_path / "lake" / "notes.txt").write_text("x")              # 文件
    assert st.list_containers() == ["c1"]


# --- lake.catalog kind 分型 ---------------------------------------------------


def test_catalog_kinds_and_container_entry(populated: Lake) -> None:
    result = populated.catalog()
    by_name = {e.name: e for e in result.datasets}
    assert by_name["gas_net"].kind == "container"
    assert by_name["gas_net"].num_rows == 5            # 3 + 2 表求和
    assert by_name["papers"].kind == "document"        # chunk_text/page_number
    assert by_name["sales"].kind == "structured"
    assert populated.catalog().total == 3


# --- API:详情带 tables / schema 二段名 ---------------------------------------


def test_detail_container_carries_tables(populated: Lake) -> None:
    client = _client(populated)
    r = client.get("/api/v1/datasets/gas_net")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "container"
    assert body["tables"] == ["segments", "stations"]

    plain = client.get("/api/v1/datasets/sales").json()
    assert plain["kind"] == "structured" and plain["tables"] is None


def test_list_endpoint_carries_kind(populated: Lake) -> None:
    client = _client(populated)
    items = {d["name"]: d for d in client.get("/api/v1/datasets").json()["datasets"]}
    assert items["gas_net"]["kind"] == "container"
    assert items["papers"]["kind"] == "document"
    assert "tables" not in items["gas_net"] or items["gas_net"]["tables"] is None


def test_schema_with_table_param(populated: Lake) -> None:
    client = _client(populated)
    r = client.get("/api/v1/datasets/gas_net/schema", params={"table": "stations"})
    assert r.status_code == 200
    assert r.json()["name"] == "gas_net"
    assert [f["name"] for f in r.json()["fields"]] == ["a"]

    # 单表集不传 table —— 行为不变
    assert client.get("/api/v1/datasets/sales/schema").status_code == 200


def test_schema_bare_container_422(populated: Lake) -> None:
    client = _client(populated)
    r = client.get("/api/v1/datasets/gas_net/schema")
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "OLAP_AMBIGUOUS_DATASET"
    assert "?table=<name>" in body["message"]
    assert "segments" in body["message"]
