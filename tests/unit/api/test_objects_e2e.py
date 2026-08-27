"""W5.1 — MS2 DoD e2e:跨源同口径自动化(F2.5,机制验证形态)。

**DoD 断言**:同一物理量经两"源系统"(segments 规范 ID + kPa 口径;
src_b 本地 ID + MPa 口径)→ Object Set 查询返回**同一对象、同一属性、
同一单位**(数值容差内)。

真链路:真 Lake(LOCAL 后端,hermetic 防 .env minio 污染)+ 真 stores
(:memory: system_db)+ 真 DuckDB 执行(容器二段名注册)。checker 用直通
stub——ACL 语义已由 test_objects_query 的接线审计测试钉住,此处验数据面。
沿 v1.11.0.1 W5.2 用户决策先例:演示数据非业务契约内容,验完即弃(tmp)。
"""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest
from arrow_lake import Lake
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.config import ArrowLakeConfig, StorageBackend, StorageConfig
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.contracts import ContractStore
from arrow_lake.system_db.stores.entity_map import EntityMapStore
from arrow_lake.system_db.stores.semantic_alignments import SemanticAlignmentStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

CONTRACT_YAML = """
dataset: demo_objects
tables:
  segments:
    object_class: 管段
    lifecycle: {column: 状态, states: [在建, 在运, 报废], initial: 在建}
    identifier:
      column: seg_id
      pattern: "GAS.SEGMENT.{区域}.{序列}"
    columns:
      - {name: 压力, label: 管段运行压力, unit: kPa}
  src_b:
    columns:
      - {name: 压力, unit: kPa}
"""

ALIGNMENT_YAML = """
dataset: demo_objects
tables:
  src_b:
    columns:
      压力: {unit: {from: MPa, to: kPa}}
"""


class _PassthroughChecker:
    """直通 checker(无 ACL 行);安全面由接线审计测试负责。"""

    def get_acl(self, dataset, role):
        return None

    def check_dataset_access(self, *, role, dataset, action, permissions=None):
        return True

    def apply_table_filter(self, table, dataset, role):
        return table


@pytest.fixture
def client(tmp_path) -> TestClient:
    base = str(tmp_path / "data")
    cfg = ArrowLakeConfig()
    cfg.storage = StorageConfig(base_uri=base, backend=StorageBackend.LOCAL)
    lake = Lake(base_uri=base, config=cfg)
    # 源系统 A:规范对象 ID 直取,kPa 口径
    lake.create_dataset("demo_objects", pa.table({
        "seg_id": ["GAS.SEGMENT.RG01.S047"],
        "压力": pa.array([2000.0], pa.float64()),
        "状态": ["在运"],
    }), table="segments")
    # 源系统 B:本地编号 + MPa 口径
    lake.create_dataset("demo_objects", pa.table({
        "压力": pa.array([2.0], pa.float64()),
        "本地编号": ["S-047"],
    }), table="src_b")

    db = SystemDB(":memory:")
    Migrator(db).run()
    ContractStore(db).save_contract("demo_objects", CONTRACT_YAML)
    SemanticAlignmentStore(db).save_alignment("demo_objects", ALIGNMENT_YAML)
    EntityMapStore(db).upsert(
        scope="demo_objects", table_name="src_b", source_system="GIS-B",
        source_id="S-047", object_id="GAS.SEGMENT.RG01.S047",
    )

    from arrow_lake.api.routers.objects import router

    app = FastAPI()
    app.state.lake = lake
    app.state.checker = _PassthroughChecker()
    app.state.contract_store = ContractStore(db)
    app.state.semantic_alignment_store = SemanticAlignmentStore(db)
    app.state.entity_map_store = EntityMapStore(db)
    app.state.ontology_rules_store = None

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(
            sub="e2e", role=Role.VIEWER, exp=0, iat=0,
        )
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as c:
        yield c
    db.close()


class TestCrossSourceSameCaliber:
    def test_dod_same_object_same_attribute_same_unit(self, client: TestClient) -> None:
        seg = client.post("/api/v1/objects/query", json={
            "dataset": "demo_objects", "object_type": "segments",
        })
        assert seg.status_code == 200, seg.text
        srcb = client.post("/api/v1/objects/query", json={
            "dataset": "demo_objects", "object_type": "src_b",
            "id_column": "本地编号",
        })
        assert srcb.status_code == 200, srcb.text
        s, b = seg.json(), srcb.json()

        # 身份双路径汇到同一对象(①契约 pattern 直取 ②entity_map 映射)
        assert s["objects"][0]["identifier"]["matched"] is True
        assert b["objects"][0]["identifier"]["mapped"] is True
        assert b["objects"][0]["object_id"] == s["objects"][0]["object_id"] \
            == "GAS.SEGMENT.RG01.S047"

        # 同一属性同一单位:src_b 2.0 MPa 经对齐投影 == segments 2000.0 kPa
        v_seg = s["objects"][0]["attributes"]["压力"]
        v_src = b["objects"][0]["attributes"]["压力"]
        assert v_seg == pytest.approx(2000.0)
        assert v_src == pytest.approx(v_seg)

        # 口径可审计:响应 aligned 元数据 + 契约 unit 一致
        assert s["aligned"] == {}
        assert b["aligned"]["压力"] == {"kind": "unit", "from": "MPa", "to": "kPa"}
        cols = {c["name"]: c for c in b["columns"]}
        assert cols["压力"]["unit"] == "kPa" == b["aligned"]["压力"]["to"]

        # 顺带:label/lifecycle 贯通
        assert {c["name"]: c for c in s["columns"]}["压力"]["label"] == "管段运行压力"
        assert s["objects"][0]["lifecycle_state"] == "在运"

    def test_filter_over_real_execution(self, client: TestClient) -> None:
        """过滤走真执行(raw 口径语义)。"""
        r = client.post("/api/v1/objects/query", json={
            "dataset": "demo_objects", "object_type": "segments",
            "filter": [{"column": "压力", "op": "gte", "value": 2500}],
        })
        assert r.status_code == 200
        assert r.json()["count"] == 0  # 2000 < 2500,raw 口径比较
