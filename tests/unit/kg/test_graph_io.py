"""Tests for HugeGraphClient graph import/export methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from arrow_lake.config import HugeGraphConfig
from arrow_lake.knowledge_graph.client import HugeGraphClient


@pytest.fixture()
def config() -> HugeGraphConfig:
    return HugeGraphConfig(enabled=True, host="localhost", port=8089, graph_name="test_graph", timeout_seconds=10.0)


@pytest.fixture()
def mock_client(config: HugeGraphConfig) -> HugeGraphClient:
    client = HugeGraphClient(config)
    client._client = AsyncMock(spec=httpx.AsyncClient)
    return client


def _mock_response(status_code: int, json_data: object) -> httpx.Response:
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


def _gremlin_response(data: list) -> dict:
    return {"result": {"data": data}, "status": {"code": 200, "message": ""}}


# ---------------------------------------------------------------------------
# Export Graph — Gremlin path (happy path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_export_graph_with_properties(mock_client: HugeGraphClient) -> None:
    vertices = [{"name": "a", "lang": "en"}, {"name": "b", "lang": "en"}]
    edges = [{"label": "knows", "outV": "1:a", "inV": "1:b"}]
    mock_client.gremlin = AsyncMock(side_effect=[
        vertices, edges,
    ])
    result = await mock_client.export_graph(with_properties=True)
    assert len(result["vertices"]) == 2
    assert len(result["edges"]) == 1


@pytest.mark.asyncio()
async def test_export_graph_without_properties(mock_client: HugeGraphClient) -> None:
    ids = ["1:a", "1:b"]
    edges = []
    mock_client.gremlin = AsyncMock(side_effect=[ids, edges])
    result = await mock_client.export_graph(with_properties=False)
    assert result["vertices"] == [{"id": "1:a"}, {"id": "1:b"}]
    assert result["edges"] == []


# ---------------------------------------------------------------------------
# Export Graph — REST API fallback (Gremlin unavailable)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_export_graph_rest_fallback_with_properties(mock_client: HugeGraphClient) -> None:
    """When Gremlin fails, export_graph should fall back to REST API."""
    mock_client.gremlin = AsyncMock(side_effect=Exception("No such property: g"))

    v_data = {"vertices": [{"id": "1:a", "label": "person", "properties": {"name": "a"}}]}
    e_data = {"edges": [{"id": "e1", "label": "knows", "outV": "1:a", "inV": "1:b"}]}
    mock_client._get = AsyncMock(side_effect=[
        _mock_response(200, v_data),
        _mock_response(200, e_data),
    ])

    result = await mock_client.export_graph(with_properties=True)
    assert len(result["vertices"]) == 1
    assert result["vertices"][0]["properties"]["name"] == "a"
    assert len(result["edges"]) == 1


@pytest.mark.asyncio()
async def test_export_graph_rest_fallback_without_properties(mock_client: HugeGraphClient) -> None:
    """REST fallback with with_properties=False should extract IDs only."""
    mock_client.gremlin = AsyncMock(side_effect=Exception("MissingPropertyException"))

    v_data = {"vertices": [{"id": "1:a", "label": "person"}, {"id": "1:b", "label": "doc"}]}
    e_data = {"edges": []}
    mock_client._get = AsyncMock(side_effect=[
        _mock_response(200, v_data),
        _mock_response(200, e_data),
    ])

    result = await mock_client.export_graph(with_properties=False)
    assert result["vertices"] == [{"id": "1:a"}, {"id": "1:b"}]
    assert result["edges"] == []


# ---------------------------------------------------------------------------
# Import Graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_import_graph_success(mock_client: HugeGraphClient) -> None:
    mock_client.add_vertices = AsyncMock(return_value=["v1", "v2"])
    mock_client.add_edges = AsyncMock(return_value=2)
    data = {
        "vertices": [{"id": "v1", "label": "person"}, {"id": "v2", "label": "person"}],
        "edges": [{"label": "knows", "outV": "v1", "outVLabel": "person", "inV": "v2", "inVLabel": "person"}],
    }
    result = await mock_client.import_graph(data)
    assert result["vertices_added"] == 2
    assert result["edges_added"] == 2


@pytest.mark.asyncio()
async def test_import_graph_empty(mock_client: HugeGraphClient) -> None:
    result = await mock_client.import_graph({"vertices": [], "edges": []})
    assert result["vertices_added"] == 0
    assert result["edges_added"] == 0
