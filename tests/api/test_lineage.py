"""Tests for lineage tracking endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.lineage_record_event.return_value = None
    lake.lineage_history.return_value = [
        {"operation": "create", "actor": "system", "timestamp": "2026-01-01T00:00:00Z"},
        {"operation": "append", "actor": "user", "timestamp": "2026-01-02T00:00:00Z"},
    ]
    lake.lineage_query.return_value = [
        {"dataset": "docs", "operation": "create"},
        {"dataset": "images", "operation": "create"},
    ]
    return lake


@pytest.fixture
async def client(mock_lake: MagicMock) -> AsyncClient:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Record lineage event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_lineage_event(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/lineage/record?dataset_name=docs",
        json={
            "operation": "transform",
            "source_datasets": ["raw_data"],
            "transform_type": "filter",
            "actor": "pipeline",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "docs" in body["message"]

    mock_lake.lineage_record_event.assert_called_once_with(
        "docs",
        "transform",
        source_datasets=["raw_data"],
        transform_type="filter",
        actor="pipeline",
        metadata=None,
    )


# ---------------------------------------------------------------------------
# Lineage history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_lineage_history(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.get("/api/v1/lineage/history/docs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["dataset_name"] == "docs"
    assert len(body["events"]) == 2
    assert body["events"][0]["operation"] == "create"

    mock_lake.lineage_history.assert_called_once_with("docs")


# ---------------------------------------------------------------------------
# Lineage query
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_lineage(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/lineage/query",
        json={"sql": "SELECT * FROM _lineage_events WHERE operation='create'"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["data"]) == 2

    mock_lake.lineage_query.assert_called_once_with(
        "SELECT * FROM _lineage_events WHERE operation='create'"
    )


@pytest.mark.asyncio
async def test_query_lineage_empty_sql_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/lineage/query",
        json={"sql": ""},
    )
    assert resp.status_code == 422
