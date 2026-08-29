"""W2.4 — POST /api/v1/annotation/dispatch 冒烟 + _bg_dispatch 全链(mock LS)。

后台链路本体由 test_dispatch_flow.py(run_dispatch 11 例)钉住;此处验:
端点校验面(404/503/422×2)+ 202 结构;_bg_dispatch 直调(同步)的
audit 成功/失败双路径。
"""

from __future__ import annotations

import io
import json
from typing import Any

import pyarrow as pa
import pytest
from arrow_lake.annotation.sampler import SampleBudget
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.api.routers.annotation import _bg_dispatch
from arrow_lake.config.annotation import AnnotationConfig
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.annotation import AnnotationProjectStore
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


class FakeLake:
    """read_dataset 返回小表;audit_record 记录事件。"""

    def __init__(self, *, with_dead: bool = False, with_text: bool = True) -> None:
        self.with_dead = with_dead
        cols, data = [], []
        if with_text:
            cols += ["text", "quality_score"]
            data += [["调压站 A 压力异常", 0.2], ["凤凰花园小区燃气泄漏", 0.8]]
        else:
            cols += ["body"]
            data += [["no text col"]]
        self.table = pa.table({c: [r[i] for r in data] for i, c in enumerate(cols)})
        self.audits: list[tuple[str, str, dict]] = []

    def read_dataset(self, name: str, **kw: Any) -> pa.Table:
        if name.endswith("_dead_letter"):
            if not self.with_dead:
                raise RuntimeError("not found")
            return pa.table({"text": ["死信行:阀门泄漏拒收"]})
        return self.table

    def audit_record(self, event: str, **kw: Any) -> str:
        self.audits.append((event, kw.get("dataset_name", ""), kw.get("payload") or {}))
        return "audit-id"


class FakeExtractor:
    async def extract(self, text: str, **kw: Any) -> Any:
        from arrow_lake.knowledge_graph.extractor import ExtractionResult

        return ExtractionResult(entities=(), relations=(), raw_text=text)

    # _bg_dispatch 经 lake._get_kg_extractor() 拿 extractor
    def _get_kg_extractor(self):  # pragma: no cover - placeholder, see FakeLakeWithHE
        return self


class FakeLakeWithHE(FakeLake):
    def _get_kg_extractor(self) -> Any:
        return FakeExtractor()


def _make_app(
    *, db: SystemDB | None, config: AnnotationConfig | None = None,
    lake: Any = None,
) -> TestClient:
    from arrow_lake.api.routers.annotation import router

    app = FastAPI()
    app.state.annotation_project_store = AnnotationProjectStore(db) if db else None
    app.state.config = type("C", (), {"annotation": config or AnnotationConfig()})()

    if lake is not None:
        app.state.lake = lake

        @app.middleware("http")
        async def _inject_lake(request: Request, call_next):
            request.state.lake = lake
            return await call_next(request)

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = TokenPayload(sub="tester", role=Role.ADMIN, exp=0, iat=0, user_id=3)
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _seed_project(db: SystemDB, *, status: str = "active") -> None:
    store = AnnotationProjectStore(db)
    rec = store.create_project(
        name="p1", dataset="ds1", template_name="t",
        labeling_config="<View/>",
    )
    assert rec is not None
    if status != "active":
        store.set_status("p1", status)


class TestEndpointValidation:
    def test_dispatch_202_shape(self, db: SystemDB) -> None:
        _seed_project(db)
        cfg = AnnotationConfig(ls_url="http://ls", ls_api_token="tok")
        client = _make_app(db=db, config=cfg, lake=FakeLakeWithHE())
        resp = client.post("/api/v1/annotation/dispatch", json={"project": "p1"})
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["task_id"]
        assert body["operation"] == "annotation_dispatch"
        assert body["candidate_rows"] == 2

    def test_unknown_project_404(self, db: SystemDB) -> None:
        cfg = AnnotationConfig(ls_url="http://ls", ls_api_token="tok")
        client = _make_app(db=db, config=cfg, lake=FakeLakeWithHE())
        assert client.post("/api/v1/annotation/dispatch", json={"project": "ghost"}).status_code == 404

    def test_ls_unconfigured_503(self, db: SystemDB) -> None:
        _seed_project(db)
        client = _make_app(db=db, config=AnnotationConfig(), lake=FakeLakeWithHE())
        assert client.post("/api/v1/annotation/dispatch", json={"project": "p1"}).status_code == 503

    def test_missing_text_column_422(self, db: SystemDB) -> None:
        _seed_project(db)
        cfg = AnnotationConfig(ls_url="http://ls", ls_api_token="tok")
        client = _make_app(db=db, config=cfg, lake=FakeLakeWithHE(with_text=False))
        resp = client.post("/api/v1/annotation/dispatch", json={"project": "p1"})
        assert resp.status_code == 422
        assert "text" in resp.json()["detail"]

    def test_closed_project_422(self, db: SystemDB) -> None:
        _seed_project(db, status="closed")
        cfg = AnnotationConfig(ls_url="http://ls", ls_api_token="tok")
        client = _make_app(db=db, config=cfg, lake=FakeLakeWithHE())
        resp = client.post("/api/v1/annotation/dispatch", json={"project": "p1"})
        assert resp.status_code == 422


class LSFakeOpener:
    """script 式 LS 响应(refresh→create→import)。"""

    def __init__(self, *, fail_import: bool = False) -> None:
        self.fail_import = fail_import
        self.imports: list[list[dict]] = []

    def __call__(self, req: Any, timeout: float = 0) -> Any:
        url = req.full_url
        if "token/refresh" in url:
            body = b'{"access": "a"}'
        elif "/api/projects" in url and req.get_method() == "POST" and "import" not in url:
            body = b'{"id": 77, "title": "p1"}'
        elif "import" in url:
            if self.fail_import:
                raise _http_error(url, 502, "boom")
            self.imports.append(json.loads(req.data.decode()))
            body = b'{"task_ids": [1]}'
        else:
            body = b"{}"
        resp = io.BytesIO(body)
        resp.status = 200
        return resp


def _http_error(url: str, code: int, msg: str) -> Any:
    import urllib.error

    return urllib.error.HTTPError(url, code, msg, {}, io.BytesIO(msg.encode()))


def _run_bg(db: SystemDB, opener: LSFakeOpener) -> tuple[Any, FakeLakeWithHE]:
    """构造 (run, lake):lake 独立持有,异常路径也能断言 audit。"""
    _seed_project(db)
    lake = FakeLakeWithHE(with_dead=True)
    app_state = type(
        "S", (), {
            "annotation_project_store": AnnotationProjectStore(db),
            "config": type("C", (), {"annotation": AnnotationConfig()})(),
        },
    )()

    def run() -> dict:
        return _bg_dispatch(
            app_state, lake, "tester", "p1",
            rows=[
                {"text": "调压站异常", "quality_score": 0.3},
                {"text": "死信行", "quality_score": None},
            ],
            text_column="text", total=2, budget=SampleBudget(),
            quality_scores={"r0": 0.3}, dead_row_ids=["r1"],
            generalize_rules=[], entity_names=[],
            ls_url="http://ls", ls_token="tok", import_batch_size=50,
            ls_opener=opener,
        )

    return run, lake


class TestBgDispatch:
    def test_success_audits_and_returns_outcome(self, db: SystemDB) -> None:
        opener = LSFakeOpener()
        run, lake = _run_bg(db, opener)
        out = run()
        assert out["dispatched"] == 2
        assert out["ls_project_id"] == 77
        assert len(opener.imports) >= 1
        events = [a[0] for a in lake.audits]
        assert "annotation.dispatch" in events
        payload = next(a[2] for a in lake.audits if a[0] == "annotation.dispatch")
        assert payload["status"] == "ok"
        assert payload["dispatched"] == 2
        # 懒绑定回写注册表
        rec = AnnotationProjectStore(db).get_project("p1")
        assert rec["ls_project_id"] == 77

    def test_ls_failure_audits_failed_and_raises(self, db: SystemDB) -> None:
        opener = LSFakeOpener(fail_import=True)
        run, lake = _run_bg(db, opener)
        with pytest.raises(Exception, match="502"):
            run()
        payload = next(a[2] for a in lake.audits if a[0] == "annotation.dispatch")
        assert payload["status"] == "failed"
        assert "502" in payload["error"]
