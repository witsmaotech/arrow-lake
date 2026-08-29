"""W4.2 — MS4 vertical slice e2e:demo 数据集全流程 L2→L4。

**DoD 断言**(version-plan ①):采样→脱敏→预标注→dispatch→(模拟)标注
→recover→ADL——机械环节无人工干预,ADL 断言(行/状态/幂等/版本)。

真链路:真 Lake(LOCAL 后端,hermetic 防 .env minio 污染)+ 真 stores
(:memory: system_db)+ 真 router(HTTP);mock 只落在两个外部边界:
LS(script opener)与 LLM(fake extractor)——与 W5 live 的差异面仅此。
沿 test_objects_e2e 先例:演示数据验完即弃(tmp)。
"""

from __future__ import annotations

import io
import json
from typing import Any

import pyarrow as pa
from arrow_lake import Lake
from arrow_lake.annotation.sampler import SampleBudget
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.api.routers.annotation import _bg_dispatch
from arrow_lake.config import ArrowLakeConfig, StorageBackend, StorageConfig
from arrow_lake.config.annotation import AnnotationConfig
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.annotation import AnnotationProjectStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

REAL_TEMPLATE = "project_concept_graph"

ROWS = [
    {"text": "凤凰花园小区发生燃气泄漏事故,应急指挥中心启动响应", "quality_score": 0.10},
    {"text": "调压站 B 出口压力异常波动", "quality_score": 0.45},
    {"text": "阀门 A 例行巡检完成,无异常", "quality_score": 0.92},
    {"text": "长江路中压管网改造工程验收通过", "quality_score": 0.60},
]


class LSScript:
    """有状态 LS:refresh→create→import(收 tasks);tasks 可后续注入标注。"""

    def __init__(self) -> None:
        self.project_id = 0
        self.imported: list[dict] = []
        self.tasks: list[dict] = []
        self._next_task_id = 1

    def __call__(self, req: Any, timeout: float = 0) -> Any:
        url = req.full_url
        method = req.get_method()
        if "token/refresh" in url:
            body = b'{"access": "tok"}'
        elif method == "POST" and url.rstrip("/").endswith("/api/projects"):
            self.project_id = 77
            body = b'{"id": 77, "title": "p1"}'
        elif "import" in url:
            batch = json.loads(req.data.decode())
            for t in batch:
                t["id"] = self._next_task_id
                self._next_task_id += 1
                t.setdefault("annotations", [])
            self.imported.extend(batch)
            self.tasks.extend(batch)
            body = json.dumps({"task_ids": [t["id"] for t in batch]}).encode()
        elif "/api/tasks" in url:
            body = json.dumps({"tasks": self.tasks, "total": len(self.tasks)}).encode()
        else:
            body = b"{}"
        resp = io.BytesIO(body)
        resp.status = 200
        return resp

    def annotate_all(self, regions_for) -> None:
        """模拟标注者:给每个未标注 task 塞一条 annotation。"""
        for t in self.tasks:
            if not t.get("annotations"):
                t["annotations"].append({
                    "id": t["id"] * 100,
                    "result": regions_for(t["data"]["text"]),
                    "completed_by": 7,
                    "created_at": "2026-08-29T08:00:00Z",
                    "was_cancelled": False, "ground_truth": False,
                })


class FakeExtractor:
    """LLM 边界替身:抽出文本中的实体(调压站/燃气泄漏事故…)。"""

    async def extract(self, text: str, **kw: Any):
        from arrow_lake.knowledge_graph.extractor import (
            ExtractedEntity,
            ExtractionResult,
        )

        known = {"调压站": "硬件", "燃气泄漏事故": "事故", "阀门": "硬件", "管网": "硬件"}
        entities = tuple(
            ExtractedEntity(name, etype) for name, etype in known.items() if name in text
        )
        return ExtractionResult(entities=entities, relations=(), raw_text=text)


def _regions(text: str) -> list[dict]:
    """模拟人工标注:每个可定位实体一个 region + scenario。"""
    out = []
    for name, etype in [("调压站", "硬件"), ("燃气泄漏事故", "事故"), ("阀门", "硬件"), ("管网", "硬件")]:
        start = text.find(name)
        if start >= 0:
            out.append({
                "id": f"e{len(out)}", "from_name": "events" if etype == "事故" else "objects",
                "to_name": "text", "type": "labels",
                "value": {"start": start, "end": start + len(name), "text": name,
                          "labels": [etype]},
            })
    out.append({"from_name": "scenario", "to_name": "text", "type": "choices",
                "value": {"choices": ["应急"]}})
    return out


class FakeAuditLake:
    """lake 的 audit 面(真 Lake 没有 audit_record?有——但经 AuditTrail 需要
    storage 初始化;e2e 用薄包装代理真 Lake 并拦截 audit)。"""

    def __init__(self, real) -> None:
        self._real = real
        self.audits: list[str] = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    def audit_record(self, event, **kw):
        self.audits.append(event)
        return "aid"


def test_vertical_slice(tmp_path, monkeypatch):
    from arrow_lake.annotation.dispatch import stable_row_id as _sid

    monkeypatch.setenv("ARROW_LAKE__MASKING__HMAC_KEY", "e2e-hmac-key")  # L3 fail-closed 前提
    base = str(tmp_path / "data")
    cfg = ArrowLakeConfig()
    cfg.storage = StorageConfig(base_uri=base, backend=StorageBackend.LOCAL)
    lake = Lake(base_uri=base, config=cfg)
    lake.create_dataset("demo_ms3_alerts", pa.table({
        "text": [r["text"] for r in ROWS],
        "quality_score": pa.array([r["quality_score"] for r in ROWS], pa.float64()),
    }))
    db = SystemDB(":memory:")
    Migrator(db).run()
    store = AnnotationProjectStore(db)
    audit_lake = FakeAuditLake(lake)
    ls = LSScript()
    app = FastAPI()
    app.state.annotation_project_store = store
    app.state.lake = audit_lake
    app.state.config = type("C", (), {
        "annotation": AnnotationConfig(ls_url="http://ls", ls_api_token="tok")})()

    @app.middleware("http")
    async def _inject(request: Request, call_next):
        request.state.user = TokenPayload(sub="tester", role=Role.ADMIN, exp=0, iat=0)
        return await call_next(request)

    from arrow_lake.api.routers.annotation import router

    app.include_router(router)
    client = TestClient(app)

    from arrow_lake.annotation import sync as sync_mod

    real_client = sync_mod.LSClient
    monkeypatch.setattr(
        sync_mod, "LSClient", lambda u, t, **kw: real_client(u, t, opener=ls, **kw))

    # ① 注册项目(真模板 gallery → 真 config 生成)
    resp = client.post("/api/v1/annotation/projects", json={
        "name": "gas-e2e", "dataset": "demo_ms3_alerts", "template_name": REAL_TEMPLATE})
    assert resp.status_code == 200, resp.text
    assert '<Label value="主体"' in resp.json()["labeling_config"]

    # ② dispatch(直调后台 worker:真 lake rows + mock LS/LLM 边界)
    async def awaitable_dispatch():
        return await _bg_dispatch(
            app.state, audit_lake, "tester", "gas-e2e",
            rows=[dict(r) for r in ROWS],
            text_column="text", total=4, budget=SampleBudget(),
            quality_scores={
                _sid(r["text"], i): r["quality_score"] for i, r in enumerate(ROWS)
            },
            dead_row_ids=None,
            generalize_rules=[("凤凰花园小区", "住宅小区<脱敏>")],  # L2 泛化
            entity_names=["应急指挥中心"],                        # L3 假名
            ls_url="http://ls", ls_token="tok", import_batch_size=50,
            ls_opener=ls,
        )

    import asyncio

    out = asyncio.run(awaitable_dispatch())
    assert out["dispatched"] == 4
    assert out["strategies"]["uncertainty"] >= 2  # 低分行先被采
    # 脱敏生效:LS 收到的文本不见原始敏感值
    texts = [t["data"]["text"] for t in ls.imported]
    assert all("凤凰花园小区" not in t and "应急指挥中心" not in t for t in texts)
    # 预标注内嵌(prediction 来自 fake HE,span 基于脱敏文本)
    assert all(t["predictions"][0]["model_version"] == "hyper-extract" for t in ls.imported)

    # ③ 模拟人工标注(标注者 7)+ 手动回收
    ls.annotate_all(_regions)
    rec = client.post("/api/v1/annotation/recover", json={"project": "gas-e2e"})
    assert rec.status_code == 200, rec.text
    summary = rec.json()
    assert summary["annotations_recovered"] == 4
    assert summary["adl_rows_written"] == 4
    assert summary["review"]["pending"] == 4  # 单标注 + min=2 → pending

    # ④ ADL 断言(真 Lance 表)
    adl = lake.read_dataset("demo_ms3_alerts_adl").to_pylist()
    assert len(adl) == 4
    assert all(row["review_status"] == "pending" for row in adl)
    assert all(row["source_dataset"] == "demo_ms3_alerts" for row in adl)
    scenario_ok = [row for row in adl if row["scenario"] == "应急"]
    assert len(scenario_ok) == 4

    # ⑤ 幂等:再次回收零重复(watermark + adl_id 双保险)
    rec2 = client.post("/api/v1/annotation/recover", json={"project": "gas-e2e"}).json()
    assert rec2["adl_rows_written"] == 0
    assert len(lake.read_dataset("demo_ms3_alerts_adl").to_pylist()) == 4

    # ⑥ 审计链
    assert "annotation.dispatch" in audit_lake.audits
    assert "annotation.recover" in audit_lake.audits

    db.close()
