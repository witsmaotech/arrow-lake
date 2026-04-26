"""Tests for HugeGraphClient Traverser API methods."""

from __future__ import annotations

from unittest.mock import AsyncMock

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


def _ok(json_data: dict) -> httpx.Response:
    return httpx.Response(status_code=200, json=json_data, request=httpx.Request("POST", "http://localhost:8089"))


# ---------------------------------------------------------------------------
# All Shortest Paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_all_shortest_paths_success(mock_client: HugeGraphClient) -> None:
    resp = _ok({"paths": [{"objects": ["1:a", "1:b"], "labels": [], "weights": []}]})
    mock_client._client.post.return_value = resp
    result = await mock_client.traverser_all_shortest_paths("1:a", "1:b")
    assert len(result) == 1
    assert result[0]["objects"] == ["1:a", "1:b"]


@pytest.mark.asyncio()
async def test_all_shortest_paths_empty(mock_client: HugeGraphClient) -> None:
    resp = _ok({"paths": []})
    mock_client._client.post.return_value = resp
    result = await mock_client.traverser_all_shortest_paths("1:a", "1:b")
    assert result == []


# ---------------------------------------------------------------------------
# Weighted Shortest Path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_weighted_shortest_path_success(mock_client: HugeGraphClient) -> None:
    resp = _ok({"path": {"objects": ["1:a", "1:b"], "weights": [0.5]}, "weight": 0.5})
    mock_client._client.post.return_value = resp
    result = await mock_client.traverser_weighted_shortest_path("1:a", "1:b", weight_prop="w")
    assert result["weight"] == 0.5


# ---------------------------------------------------------------------------
# Single Source Shortest Path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_single_source_shortest_path(mock_client: HugeGraphClient) -> None:
    resp = _ok({"paths": {"1:b": {"objects": ["1:a", "1:b"], "weights": [0.2]}}})
    mock_client._client.post.return_value = resp
    result = await mock_client.traverser_single_source_shortest_path("1:a")
    assert "1:b" in result["paths"]


# ---------------------------------------------------------------------------
# Multi Node Shortest Path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_multi_node_shortest_path(mock_client: HugeGraphClient) -> None:
    resp = _ok({"paths": [{"source": "1:a", "target": "2:c", "path": {"objects": ["1:a", "2:c"], "weights": []}}]})
    mock_client._client.post.return_value = resp
    result = await mock_client.traverser_multi_node_shortest_path(["1:a"], ["2:c"])
    assert len(result) == 1
    assert result[0]["source"] == "1:a"


# ---------------------------------------------------------------------------
# Rays
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rays_success(mock_client: HugeGraphClient) -> None:
    resp = _ok({"rays": [{"objects": ["1:a", "2:b"], "labels": ["knows"]}]})
    mock_client._client.post.return_value = resp
    result = await mock_client.traverser_rays("1:a")
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Rings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rings_success(mock_client: HugeGraphClient) -> None:
    resp = _ok({"rings": [{"objects": ["1:a", "1:b", "1:a"], "labels": ["knows", "knows"]}]})
    mock_client._client.post.return_value = resp
    result = await mock_client.traverser_rings("1:a")
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Crosspoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_crosspoints_success(mock_client: HugeGraphClient) -> None:
    cp_vertex = {"id": "2:m", "label": "software", "type": "vertex", "properties": {"name": "m"}}
    resp = _ok({"crosspoints": [{"vertex": cp_vertex, "crossed_paths": []}]})
    mock_client._client.post.return_value = resp
    result = await mock_client.traverser_crosspoints("1:a", "1:b")
    assert len(result) == 1
    assert result[0]["vertex"]["id"] == "2:m"


# ---------------------------------------------------------------------------
# Customized Paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_customized_paths_success(mock_client: HugeGraphClient) -> None:
    resp = _ok({"paths": [{"objects": [{"id": "1:a"}, {"id": "2:b"}, {"id": "1:c"}]}]})
    mock_client._client.post.return_value = resp
    steps = [{"direction": "OUT", "labels": ["knows"]}]
    result = await mock_client.traverser_customized_paths("1:a", steps)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_traverser_http_error_raises(mock_client: HugeGraphClient) -> None:
    mock_client._client.post.side_effect = httpx.ConnectError("refused")
    from arrow_lake.exceptions import KGError

    with pytest.raises(KGError):
        await mock_client.traverser_all_shortest_paths("1:a", "1:b")


@pytest.mark.asyncio()
async def test_traverser_non_200_raises(mock_client: HugeGraphClient) -> None:
    resp = httpx.Response(status_code=500, json={"error": "internal"}, request=httpx.Request("POST", "http://localhost:8089"))
    mock_client._client.post.return_value = resp
    from arrow_lake.exceptions import KGError

    with pytest.raises(KGError):
        await mock_client.traverser_all_shortest_paths("1:a", "1:b")
