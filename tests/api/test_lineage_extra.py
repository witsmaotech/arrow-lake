"""Tests for lineage endpoints: graph, impact, stats (supplements test_lineage.py)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.lineage_record_event.return_value = None
    lake.lineage_history.return_value = []
    lake.lineage_query.return_value = []
    lake.lineage_graph.return_value = {
        "nodes": [
            {"id": "raw_data", "depth": 0, "type": "source"},
            {"id": "processed", "depth": 1, "type": "derived"},
        ],
        "edges": [
            {"from": "raw_data", "to": "processed", "operation": "transform"},
        ],
        "stats": {"total_nodes": 2, "total_edges": 1, "max_depth": 1},
    }
    lake.lineage_impact.return_value = [
        {"dataset": "report_a", "depth": 1, "operation": "aggregate"},
        {"dataset": "dashboard", "depth": 2, "operation": "query"},
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
# GET /api/v1/lineage/graph/{dataset_name}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lineage_graph(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.get("/api/v1/lineage/graph/docs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["dataset_name"] == "docs"
    assert len(body["nodes"]) == 2
    assert len(body["edges"]) == 1
    assert body["stats"]["total_nodes"] == 2
    assert body["stats"]["total_edges"] == 1
    assert body["stats"]["max_depth"] == 1

    mock_lake.lineage_graph.assert_called_once_with("docs", max_depth=10)


@pytest.mark.asyncio
async def test_lineage_graph_custom_depth(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.get("/api/v1/lineage/graph/docs?max_depth=5")
    assert resp.status_code == 200
    mock_lake.lineage_graph.assert_called_once_with("docs", max_depth=5)


@pytest.mark.asyncio
async def test_lineage_graph_empty(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.lineage_graph.return_value = {"nodes": [], "edges": [], "stats": {}}
    resp = await client.get("/api/v1/lineage/graph/solo_ds")
    assert resp.status_code == 200
    body = resp.json()
    assert body["nodes"] == []
    assert body["edges"] == []


# ---------------------------------------------------------------------------
# POST /api/v1/lineage/impact
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lineage_impact(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/lineage/impact",
        json={"dataset_name": "raw_data"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["source_dataset"] == "raw_data"
    assert len(body["impacted_datasets"]) == 2
    assert body["impacted_datasets"][0]["dataset"] == "report_a"
    assert body["impacted_datasets"][0]["depth"] == 1

    mock_lake.lineage_impact.assert_called_once_with("raw_data")


@pytest.mark.asyncio
async def test_lineage_impact_empty(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.lineage_impact.return_value = []
    resp = await client.post(
        "/api/v1/lineage/impact",
        json={"dataset_name": "leaf_ds"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["impacted_datasets"] == []


@pytest.mark.asyncio
async def test_lineage_impact_empty_dataset_name_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/lineage/impact",
        json={"dataset_name": ""},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/lineage/stats
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lineage_stats(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.lineage_history.return_value = [
        {"dataset_name": "docs", "operation": "create"},
        {"dataset_name": "images", "operation": "create"},
        {"dataset_name": "docs", "operation": "append"},
    ]
    resp = await client.get("/api/v1/lineage/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["total_datasets_tracked"] == 2
    assert body["total_events"] == 3

    mock_lake.lineage_history.assert_called_once_with("__all__")


@pytest.mark.asyncio
async def test_lineage_stats_empty(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.lineage_history.return_value = []
    resp = await client.get("/api/v1/lineage/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_datasets_tracked"] == 0
    assert body["total_events"] == 0


# ---------------------------------------------------------------------------
# Lineage history with object-like events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lineage_history_with_objects(client: AsyncClient, mock_lake: MagicMock) -> None:
    @dataclass
    class FakeEvent:
        dataset_name: str = "docs"
        operation: str = "create"

    mock_lake.lineage_history.return_value = [
        {"operation": "create", "actor": "system"},
        FakeEvent(),
    ]
    resp = await client.get("/api/v1/lineage/history/docs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) == 2


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lineage_graph_requires_auth(mock_lake: MagicMock) -> None:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/api/v1/lineage/graph/docs")
    assert resp.status_code in (401, 403)
