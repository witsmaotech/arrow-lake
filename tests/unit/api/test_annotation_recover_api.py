"""W3.4 — POST /api/v1/annotation/recover + GET /projects/{name}/adl。

mock LS(script opener)真解析/仲裁/ADL 构造(内存 FakeStorage);验证:
增量 watermark 推进、adl_id 幂等(两次 recover 同任务零重复)、
arbitration 分流、audit 事件、404/503/422 校验面。
"""

from __future__ import annotations

import io
import json
from typing import Any

import pyarrow as pa
import pytest
from arrow_lake.api.auth_models import Role, TokenPayload
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


REGIONS_A = [
    {"id": "e0", "from_name": "objects", "to_name": "text", "type": "labels",
     "value": {"start": 0, "end": 3, "text": "调压站", "labels": ["硬件"]}},
    {"from_name": "scenario", "to_name": "text", "type": "choices",
     "value": {"choices": ["应急"]}},
]
REGIONS_B = [  # 同 task 不同标注者,scenario 不同 → 分歧 → arbitration
    {"id": "e0", "from_name": "objects", "to_name": "text", "type": "labels",
     "value": {"start": 0, "end": 3, "text": "调压站", "labels": ["硬件"]}},
    {"from_name": "scenario", "to_name": "text", "type": "choices",
     "value": {"choices": ["常规"]}},
]


def _task(task_id: int, annotations: list[dict]) -> dict:
    return {
        "id": task_id, "data": {"text": "…", "row_id": "r1", "strategy": "uncertainty"},
        "annotations": annotations,
    }


def _ann(result: list[dict], completed_by: int) -> dict:
    return {"id": 1 if result else 0, "result": result, "completed_by": completed_by,
            "created_at": "2026-08-29T08:00:00Z", "was_cancelled": False,
            "ground_truth": False}


class LSScript:
    """LS opener:token/refresh → tasks 列表(script 可换)。"""

    def __init__(self) -> None:
        self.tasks: list[dict] = []

    def __call__(self, req: Any, timeout: float = 0) -> Any:
        url = req.full_url
        if "token/refresh" in url:
            body = b'{"access": "a"}'
        elif "/api/tasks" in url:
            body = json.dumps({"tasks": self.tasks, "total": len(self.tasks)}).encode()
        else:
            body = b"{}"
        resp = io.BytesIO(body)
        resp.status = 200
        return resp


class FakeStorage:
    def __init__(self) -> None:
        self.tables: dict[str, pa.Table] = {}

    def dataset_exists(self, name: str) -> bool:
        return name in self.tables

    def append_dataset(self, name: str, table: pa.Table) -> None:
        self.tables[name] = pa.concat_tables([self.tables[name], table])

    def create_dataset(self, name: str, table: pa.Table) -> None:
        self.tables[name] = table


class FakeLake:
    """read_dataset 走 FakeStorage;audit 记录;_get_storage 返回 manager。"""

    def __init__(self, storage: FakeStorage) -> None:
        self.storage = storage
        self.audits: list[tuple[str, dict]] = []

    def read_dataset(self, name: str, **kw: Any) -> pa.Table:
        table = self.storage.tables.get(name)
        if table is None:
            raise RuntimeError(f"no table {name}")
        return table

    def audit_record(self, event: str, **kw: Any) -> str:
        self.audits.append((event, kw.get("payload") or {}))
        return "aid"

    def _get_storage(self) -> FakeStorage:
        return self.storage


def _make_app(db: SystemDB, lake: FakeLake, ls: LSScript, *, bound: bool = True) -> TestClient:
    from arrow_lake.api.routers.annotation import router

    store = AnnotationProjectStore(db)
    rec = store.create_project(
        name="p1", dataset="ds1", template_name="t", labeling_config="<View/>")
    assert rec is not None
    if bound:
        store.set_ls_project_id("p1", 42)

    app = FastAPI()
    app.state.annotation_project_store = store
    app.state.lake = lake
    app.state.config = type("C", (), {
        "annotation": AnnotationConfig(ls_url="http://ls", ls_api_token="tok")})()

    @app.middleware("http")
    async def _inject(request: Request, call_next):
        request.state.user = TokenPayload(sub="tester", role=Role.ADMIN, exp=0, iat=0)
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _patch_ls(ls: LSScript, monkeypatch: pytest.MonkeyPatch) -> None:
    """让 recover 端点(函数内 import)拿到的 LSClient 用 script opener。"""

    from arrow_lake.annotation import dispatch as dispatch_mod

    real_client = dispatch_mod.LSClient

    def patched(base_url, token, **kw):
        kw.setdefault("opener", ls)
        return real_client(base_url, token, **kw)

    monkeypatch.setattr(dispatch_mod, "LSClient", patched)


class TestRecover:
    def test_recover_writes_adl_and_advances_watermark(self, db, monkeypatch):
        ls = LSScript()
        ls.tasks = [_task(1, [_ann(REGIONS_A, 7)])]
        storage = FakeStorage()
        lake = FakeLake(storage)
        client = _make_app(db, lake, ls)
        _patch_ls(ls, monkeypatch)

        resp = client.post("/api/v1/annotation/recover", json={"project": "p1"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["annotations_recovered"] == 1
        assert body["adl_rows_written"] == 1
        assert body["watermark"] == 1
        assert body["review"]["pending"] == 1  # 单标注 + min_annotators=2 → pending
        assert "ds1_adl" in storage.tables
        assert any(e == "annotation.recover" for e, _ in lake.audits)

    def test_second_recover_idempotent(self, db, monkeypatch):
        ls = LSScript()
        ls.tasks = [_task(1, [_ann(REGIONS_A, 7)])]
        storage = FakeStorage()
        lake = FakeLake(storage)
        client = _make_app(db, lake, ls)
        _patch_ls(ls, monkeypatch)
        client.post("/api/v1/annotation/recover", json={"project": "p1"})
        body2 = client.post("/api/v1/annotation/recover", json={"project": "p1"}).json()
        assert body2["adl_rows_written"] == 0      # adl_id 幂等
        assert body2["annotations_recovered"] == 0  # watermark 已过
        assert storage.tables["ds1_adl"].num_rows == 1

    def test_discordant_goes_arbitration(self, db, monkeypatch):
        ls = LSScript()
        ls.tasks = [_task(1, [_ann(REGIONS_A, 7), _ann(REGIONS_B, 8)])]
        storage = FakeStorage()
        lake = FakeLake(storage)
        client = _make_app(db, lake, ls)
        _patch_ls(ls, monkeypatch)
        body = client.post("/api/v1/annotation/recover", json={"project": "p1"}).json()
        assert body["review"]["arbitration"] == 1
        row = storage.tables["ds1_adl"].to_pylist()[0]
        assert row["review_status"] == "arbitration"

    def test_unbound_project_422(self, db, monkeypatch):
        ls = LSScript()
        client = _make_app(db, FakeLake(FakeStorage()), ls, bound=False)
        _patch_ls(ls, monkeypatch)
        resp = client.post("/api/v1/annotation/recover", json={"project": "p1"})
        assert resp.status_code == 422
        assert "dispatch first" in resp.json()["detail"]

    def test_unknown_project_404(self, db, monkeypatch):
        ls = LSScript()
        client = _make_app(db, FakeLake(FakeStorage()), ls)
        _patch_ls(ls, monkeypatch)
        assert client.post("/api/v1/annotation/recover", json={"project": "ghost"}).status_code == 404


class TestAdlEndpoint:
    def test_adl_rows_visible(self, db, monkeypatch):
        ls = LSScript()
        ls.tasks = [_task(1, [_ann(REGIONS_A, 7)])]
        storage = FakeStorage()
        lake = FakeLake(storage)
        client = _make_app(db, lake, ls)
        _patch_ls(ls, monkeypatch)
        client.post("/api/v1/annotation/recover", json={"project": "p1"})
        resp = client.get("/api/v1/annotation/projects/p1/adl")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        row = body["rows"][0]
        assert row["source_row_id"] == "r1"
        assert row["scenario"] == "应急"

    def test_no_adl_yet_404(self, db):
        client = _make_app(db, FakeLake(FakeStorage()), LSScript())
        assert client.get("/api/v1/annotation/projects/p1/adl").status_code == 404


class TestWebhook:
    def _app(self, db: SystemDB, lake: FakeLake | None = None) -> TestClient:
        return _make_app(db, lake or FakeLake(FakeStorage()), LSScript())

    def test_annotation_created_accepted_and_audited(self, db):
        lake = FakeLake(FakeStorage())
        client = self._app(db, lake)
        payload = {
            "action": "ANNOTATION_CREATED",
            "annotation": _ann(REGIONS_A, 7),
            "task": _task(3, []),
        }
        resp = client.post("/api/v1/annotation/webhook", json=payload)
        assert resp.status_code == 200
        assert resp.json()["accepted"] is True
        assert any(e == "annotation.webhook" for e, _ in lake.audits)

    def test_unrelated_action_not_accepted(self, db):
        client = self._app(db)
        resp = client.post("/api/v1/annotation/webhook", json={"action": "TASK_CREATED"})
        assert resp.json()["accepted"] is False

    def test_garbage_body_not_accepted(self, db):
        client = self._app(db)
        resp = client.post(
            "/api/v1/annotation/webhook",
            content=b"not json", headers={"Content-Type": "application/json"},
        )
        assert resp.json()["accepted"] is False
