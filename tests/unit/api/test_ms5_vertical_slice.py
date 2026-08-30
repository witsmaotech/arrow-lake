"""W4.5 — MS5 vertical slice e2e:评估 → 门 → 发布 → 规格书 → 四形态 → 血缘。

DoD 断言(version-plan §4):全链在**真 Lake**(LOCAL 后端 hermetic)+
真 stores(:memory: system_db)上走通;LS/LLM 边界不在链上(relevance
用 ADL 真值行,pretrain 走 KG 关闭降级路径)。红线④脱敏由 corpus 单测
钉住,此处断言导出产物结构与全链事件。
"""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest
from arrow_lake import Lake
from arrow_lake.annotation.adl import ADL_SCHEMA
from arrow_lake.annotation.dispatch import stable_row_id
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.api.deps import get_lake
from arrow_lake.config import ArrowLakeConfig, StorageBackend, StorageConfig
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.annotation import AnnotationProjectStore
from arrow_lake.system_db.stores.contracts import ContractStore
from arrow_lake.system_db.stores.drift_baselines import DriftBaselineStore
from arrow_lake.system_db.stores.quality_reports import QualityReportStore
from arrow_lake.system_db.stores.releases import ReleaseStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

CONTRACT = """
dataset: ms5_e2e
tables:
  ms5_e2e:
    columns:
      - name: text
        required: true
"""

TEXTS = [
    "阀门泄漏处置:巡检上报压力异常",
    "管道第三方施工破坏预警",
    "厨房燃气灶打不着火求助",
    "调压站巡检记录正常",
]


def _adl_table(texts: list[str]) -> pa.Table:
    rows: list[dict] = []
    for i, text in enumerate(texts):
        rid = stable_row_id(text, i)
        span = [{"label": "阀门", "start": 0, "end": 2}] if i == 0 else []
        # 双标注一致(κ=1)+ approved(黄金/SFT 可用)
        for annotator in ("ann1", "ann2"):
            rows.append({
                "adl_id": f"{rid}-{annotator}", "source_dataset": "ms5_e2e",
                "source_row_id": rid, "objects": span, "events": [],
                "rules_applied": ["r1"], "scenario": "泄漏处置",
                "relations": [],
                "annotator_id": annotator,
                "annotated_at": "2026-08-30T00:00:00+00:00",
                "review_status": "approved", "reviewer_id": "",
                "batch_id": "e2e", "adl_version": 1,
            })
        # 相关性(Choices-only,人工)
        rows.append({
            "adl_id": f"{rid}-rel", "source_dataset": "ms5_e2e",
            "source_row_id": rid, "objects": [], "events": [],
            "rules_applied": [], "scenario": "高相关", "relations": [],
            "annotator_id": "ann1",
            "annotated_at": "2026-08-30T00:00:00+00:00",
            "review_status": "approved", "reviewer_id": "",
            "batch_id": "e2e", "adl_version": 1,
        })
    return pa.Table.from_pylist(rows, schema=ADL_SCHEMA)


@pytest.fixture
def client(tmp_path) -> TestClient:
    base = str(tmp_path / "lake")
    cfg = ArrowLakeConfig()
    cfg.storage = StorageConfig(base_uri=base, backend=StorageBackend.LOCAL)
    lake = Lake(base_uri=base, config=cfg)
    lake.create_dataset("ms5_e2e", pa.table({
        "text": pa.array(TEXTS, pa.string()),
        "severity": pa.array(["high", "low"] * 2, pa.string()),
    }))
    lake.create_dataset("ms5_e2e_adl", _adl_table(TEXTS))

    db = SystemDB(":memory:")
    Migrator(db).run()
    ContractStore(db).save_contract("ms5_e2e", CONTRACT)

    app = FastAPI()
    app.state.quality_report_store = QualityReportStore(db)
    app.state.drift_baseline_store = DriftBaselineStore(db)
    app.state.release_store = ReleaseStore(db)
    app.state.contract_store = ContractStore(db)
    app.state.annotation_project_store = AnnotationProjectStore(db)
    app.state.config = SimpleNamespace(
        export=SimpleNamespace(base_dir=str(tmp_path / "exports")))

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(
            sub="e2e-admin", role=Role.ADMIN, exp=0, iat=0)
        return await call_next(request)

    app.dependency_overrides[get_lake] = lambda: lake

    from arrow_lake.api.routers.quality_report import router as quality_router
    from arrow_lake.api.routers.release import router as release_router

    app.include_router(quality_router)
    app.include_router(release_router)
    return TestClient(app)


def test_ms5_vertical_slice(client: TestClient) -> None:
    # ① 评估:双标注一致 → κ=1;relevance 全高相关;无时间列 → 降级
    r = client.post("/api/v1/quality/assess/ms5_e2e")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["dimensions"]["accuracy"]["score"] == 100.0
    assert rep["dimensions"]["relevance"]["score"] == 100.0
    assert rep["degraded"] == ["timeliness"]
    assert rep["verdict"] == "degraded"
    # 重归一:(20+35+20+7.5)/0.9 = 91.67 → bronze
    assert rep["admission"] == "bronze"

    # ② 发布门:过门(无否决/≥bronze/无前版)→ 锁版本+tag
    rel = client.post("/api/v1/release/ms5_e2e",
                      json={"changelog": "e2e 首版", "category": "project"})
    assert rel.status_code == 200, rel.text
    rel_body = rel.json()
    assert rel_body["tag"] == "v1.0.0"
    assert rel_body["lance_version"] >= 1  # 真 Lance 版本号
    assert rel_body["forced"] is False

    # ③ 规格书:YAML 导出含质量摘要与标注统计
    sheet = client.get("/api/v1/release/ms5_e2e/datasheet")
    assert sheet.status_code == 200
    assert "version: v1.0.0" in sheet.text
    assert "kappa: 1.0" in sheet.text
    assert "coverage: 1.0" in sheet.text  # 4/4 行有标注

    # ④ 语料:sft/golden 非空;rlhf 空带 note;pretrain KG 关 → 空+note
    sft = client.post("/api/v1/release/ms5_e2e/corpus?form=sft", json={"generalize_rules": [[r"\d{6,}", "[编号]"]]})
    assert sft.status_code == 200 and sft.json()["records"] == 4
    gold = client.post("/api/v1/release/ms5_e2e/corpus?form=golden", json={"generalize_rules": [[r"\d{6,}", "[编号]"]]})
    assert gold.json()["records"] == 4
    rlhf = client.post("/api/v1/release/ms5_e2e/corpus?form=rlhf", json={"generalize_rules": [[r"\d{6,}", "[编号]"]]})
    assert rlhf.json()["records"] == 0 and rlhf.json()["note"]
    pre = client.post("/api/v1/release/ms5_e2e/corpus?form=pretrain", json={})
    assert pre.json()["records"] == 0 and pre.json()["note"]

    # ⑤ 审计 + 血缘全链三事件(真 Lake 落库)
    lake: Lake = client.app.dependency_overrides[get_lake]()
    audit_types = {
        e.event_type
        for e in lake.audit_query(dataset_name="ms5_e2e")
    }
    assert {"quality.assess", "release.published", "corpus.exported"} <= audit_types
    lineage_ops = [
        getattr(e, "operation", None) or e.get("operation")
        for e in lake.lineage_history("ms5_e2e")
    ]
    assert {"quality.assessed", "release.published", "corpus.exported"} <= set(
        op for op in lineage_ops if op)
