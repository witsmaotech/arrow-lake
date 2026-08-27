"""Tests for the async data-prep endpoints (data-prep · WS3).

llm_label / extract return HTTP 202 + task_id immediately; the background work
runs via TaskManager. ``run_background`` is patched to a no-op so the endpoint
is exercised in isolation from the real LLM/service.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.api.routers import quality as quality_router
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mock_lake() -> MagicMock:
    return MagicMock()


@pytest.fixture
async def client(mock_lake: MagicMock) -> AsyncClient:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    # llm_label/extract 是 EDITOR 端点;默认 VIEWER 共享 key 会 403(存量腐烂修)
    config.api.api_key_default_role = "EDITOR"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_llm_label_returns_task_id(client: AsyncClient, monkeypatch) -> None:
    """202 + task_id + operation, without actually running the LLM."""
    ran = {}

    async def _noop_run_background(task_id, fn, *a, **kw):
        ran["task_id"] = task_id

    monkeypatch.setattr(quality_router.TaskManager, "run_background", _noop_run_background)

    resp = await client.post(
        "/api/v1/datasets/docs/quality/llm_label",
        json={
            "column": "text_content",
            "new_column": "sentiment",
            "prompt_template": "情感：{text}",
            "concurrency": 4,
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["operation"] == "llm_label"
    assert body["task_id"]
    assert "sentiment" in body["message"]
    assert ran["task_id"] == body["task_id"]


@pytest.mark.asyncio
async def test_extract_returns_task_id(client: AsyncClient, monkeypatch) -> None:
    async def _noop_run_background(task_id, fn, *a, **kw):
        return None

    monkeypatch.setattr(quality_router.TaskManager, "run_background", _noop_run_background)

    resp = await client.post(
        "/api/v1/datasets/docs/quality/extract",
        json={
            "column": "text_content",
            "fields": [
                {"name": "日期", "type": "string", "description": "提及的日期"},
                {"name": "金额", "type": "number"},
            ],
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["operation"] == "extract"
    assert body["task_id"]
    assert "2 fields" in body["message"]


@pytest.mark.asyncio
async def test_extract_rejects_empty_fields(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/quality/extract",
        json={"column": "text_content", "fields": []},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_llm_label_rejects_missing_column(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/quality/llm_label",
        json={"new_column": "sentiment", "prompt_template": "{text}"},
    )
    assert resp.status_code == 422
