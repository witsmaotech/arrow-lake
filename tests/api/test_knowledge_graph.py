"""API tests for knowledge graph endpoints — Gremlin safety + RBAC."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.kg_build = AsyncMock(return_value="task-1")
    lake.kg_build_status = AsyncMock(
        return_value={"task_id": "task-1", "status": "completed", "dataset_name": "docs"},
    )
    lake.kg_query = AsyncMock(return_value=[{"id": "v1", "label": "entity"}])
    lake.kg_get_neighbors = AsyncMock(return_value=[])
    lake.kg_stats = AsyncMock(return_value={"total_vertices": 100, "total_edges": 300})
    lake.kg_delete_graph = AsyncMock(return_value=None)
    lake.rag_query = AsyncMock(return_value={"answer": "test", "citations": []})
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
# Gremlin safety tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_rejects_closure_syntax(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/kg/query",
        json={"gremlin": "g.V().map{it.get()}'"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_query_rejects_drop(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/kg/query",
        json={"gremlin": "g.V().drop()"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_query_rejects_bare_drop(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/kg/query",
        json={"gremlin": "g.V().drop"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_query_rejects_line_comment_bypass(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/kg/query",
        json={"gremlin": "g.V()//\n.drop()"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_query_accepts_valid_gremlin(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/kg/query",
        json={"gremlin": "g.V().hasLabel('person').count()"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_kg(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/kg/build",
        json={"dataset_name": "docs"},
    )
    assert resp.status_code == 200
    assert resp.json()["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_build_status(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.get("/api/v1/kg/build/task-1/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"


@pytest.mark.asyncio
async def test_stats(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.get("/api/v1/kg/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_vertices"] == 100


@pytest.mark.asyncio
async def test_neighbors(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.get("/api/v1/kg/entities/v1/neighbors")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_delete_graph(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.delete("/api/v1/kg/graph")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_no_auth() -> None:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.post(
            "/api/v1/kg/build",
            json={"dataset_name": "docs"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_schema_endpoint(client: AsyncClient, mock_lake: MagicMock) -> None:
    kg_client = MagicMock()
    kg_client.get_schema = AsyncMock(return_value={"vertexlabels": [], "edgelabels": []})
    mock_lake._get_kg_client.return_value = kg_client

    resp = await client.get("/api/v1/kg/schema")
    assert resp.status_code == 200
    body = resp.json()
    assert "vertex_labels" in body
    assert "edge_labels" in body
