"""Tests for HugeGraphClient Traverser API methods."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from arrow_lake.config import HugeGraphConfig
from arrow_lake.exceptions import KGError
from arrow_lake.knowledge_graph.client import HugeGraphClient


@pytest.fixture()
def config() -> HugeGraphConfig:
    return HugeGraphConfig(enabled=True, host="localhost", port=8089, graph_name="test_graph", timeout_seconds=10.0)


@pytest.fixture()
def mock_client(config: HugeGraphConfig) -> HugeGraphClient:
    client = HugeGraphClient(config)
    client._client = AsyncMock(spec=httpx.AsyncClient)
    return client


def _ok_post(json_data: dict) -> httpx.Response:
    return httpx.Response(status_code=200, json=json_data, request=httpx.Request("POST", "http://localhost:8089"))


def _ok_get(json_data: dict) -> httpx.Response:
    return httpx.Response(status_code=200, json=json_data, request=httpx.Request("GET", "http://localhost:8089"))


# ---------------------------------------------------------------------------
# All Shortest Paths (GET with JSON-encoded params)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_all_shortest_paths_success(mock_client: HugeGraphClient) -> None:
    resp = _ok_get({"paths": [{"objects": ["1:a", "1:b"], "labels": [], "weights": []}]})
    mock_client._client.get.return_value = resp
    result = await mock_client.traverser_all_shortest_paths("1:a", "1:b")
    assert len(result) == 1
    assert result[0]["objects"] == ["1:a", "1:b"]
    mock_client._client.get.assert_called_once()
    call_kwargs = mock_client._client.get.call_args
    assert "allshortestpaths" in call_kwargs[0][0]
    assert call_kwargs[1]["params"]["source"] == '"1:a"'


@pytest.mark.asyncio()
async def test_all_shortest_paths_empty(mock_client: HugeGraphClient) -> None:
    resp = _ok_get({"paths": []})
    mock_client._client.get.return_value = resp
    result = await mock_client.traverser_all_shortest_paths("1:a", "1:b")
    assert result == []


# ---------------------------------------------------------------------------
# Weighted Shortest Path (GET)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_weighted_shortest_path_success(mock_client: HugeGraphClient) -> None:
    resp = _ok_get({"path": {"objects": ["1:a", "1:b"], "weights": [0.5]}, "weight": 0.5})
    mock_client._client.get.return_value = resp
    result = await mock_client.traverser_weighted_shortest_path("1:a", "1:b", weight_prop="w")
    assert result["weight"] == 0.5
    mock_client._client.get.assert_called_once()


# ---------------------------------------------------------------------------
# Single Source Shortest Path (GET)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_single_source_shortest_path(mock_client: HugeGraphClient) -> None:
    resp = _ok_get({"paths": {"1:b": {"objects": ["1:a", "1:b"], "weights": [0.2]}}})
    mock_client._client.get.return_value = resp
    result = await mock_client.traverser_single_source_shortest_path("1:a")
    assert "1:b" in result["paths"]
    mock_client._client.get.assert_called_once()


# ---------------------------------------------------------------------------
# Multi Node Shortest Path (not supported in HugeGraph 1.7.0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_multi_node_shortest_path_unsupported(mock_client: HugeGraphClient) -> None:
    from arrow_lake.exceptions import KGError

    with pytest.raises(KGError, match="not supported"):
        await mock_client.traverser_multi_node_shortest_path(["1:a"], ["2:c"])


# ---------------------------------------------------------------------------
# Rays (GET)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rays_success(mock_client: HugeGraphClient) -> None:
    resp = _ok_get({"rays": [{"objects": ["1:a", "2:b"], "labels": ["knows"]}]})
    mock_client._client.get.return_value = resp
    result = await mock_client.traverser_rays("1:a")
    assert len(result) == 1
    mock_client._client.get.assert_called_once()


# ---------------------------------------------------------------------------
# Rings (GET)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rings_success(mock_client: HugeGraphClient) -> None:
    resp = _ok_get({"rings": [{"objects": ["1:a", "1:b", "1:a"], "labels": ["knows", "knows"]}]})
    mock_client._client.get.return_value = resp
    result = await mock_client.traverser_rings("1:a")
    assert len(result) == 1
    mock_client._client.get.assert_called_once()


# ---------------------------------------------------------------------------
# Crosspoints (GET)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_crosspoints_success(mock_client: HugeGraphClient) -> None:
    cp_vertex = {"id": "2:m", "label": "software", "type": "vertex", "properties": {"name": "m"}}
    resp = _ok_get({"crosspoints": [{"vertex": cp_vertex, "crossed_paths": []}]})
    mock_client._client.get.return_value = resp
    result = await mock_client.traverser_crosspoints("1:a", "1:b")
    assert len(result) == 1
    assert result[0]["vertex"]["id"] == "2:m"
    mock_client._client.get.assert_called_once()


# ---------------------------------------------------------------------------
# Customized Paths (POST — unchanged)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_customized_paths_success(mock_client: HugeGraphClient) -> None:
    resp = _ok_post({"paths": [{"objects": [{"id": "1:a"}, {"id": "2:b"}, {"id": "1:c"}]}]})
    mock_client._client.post.return_value = resp
    steps = [{"direction": "OUT", "labels": ["knows"]}]
    result = await mock_client.traverser_customized_paths("1:a", steps)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Error handling (GET-based endpoints)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_traverser_http_error_raises(mock_client: HugeGraphClient) -> None:
    mock_client._client.get.side_effect = httpx.ConnectError("refused")
    with pytest.raises(KGError):
        await mock_client.traverser_all_shortest_paths("1:a", "1:b")


@pytest.mark.asyncio()
async def test_traverser_non_200_raises(mock_client: HugeGraphClient) -> None:
    resp = httpx.Response(status_code=500, json={"error": "internal"}, request=httpx.Request("GET", "http://localhost:8089"))
    mock_client._client.get.return_value = resp
    with pytest.raises(KGError):
        await mock_client.traverser_all_shortest_paths("1:a", "1:b")


# ---------------------------------------------------------------------------
# Weighted Shortest Path — error cases
# ---------------------------------------------------------------------------


class TestWeightedShortestPathErrors:
    @pytest.mark.asyncio()
    async def test_http_error(self, mock_client: HugeGraphClient) -> None:
        mock_client._client.get.side_effect = httpx.ConnectError("timeout")
        with pytest.raises(KGError):
            await mock_client.traverser_weighted_shortest_path("1:a", "1:b")

    @pytest.mark.asyncio()
    async def test_non_200_status(self, mock_client: HugeGraphClient) -> None:
        resp = httpx.Response(
            status_code=400, json={"error": "bad request"},
            request=httpx.Request("GET", "http://localhost:8089"),
        )
        mock_client._client.get.return_value = resp
        with pytest.raises(KGError, match="Weighted shortest path failed"):
            await mock_client.traverser_weighted_shortest_path("1:a", "1:b")


# ---------------------------------------------------------------------------
# Single Source Shortest Path — error cases
# ---------------------------------------------------------------------------


class TestSingleSourceShortestPathErrors:
    @pytest.mark.asyncio()
    async def test_http_error(self, mock_client: HugeGraphClient) -> None:
        mock_client._client.get.side_effect = httpx.ConnectError("reset")
        with pytest.raises(KGError):
            await mock_client.traverser_single_source_shortest_path("1:a")

    @pytest.mark.asyncio()
    async def test_non_200_status(self, mock_client: HugeGraphClient) -> None:
        resp = httpx.Response(
            status_code=404, json={"error": "not found"},
            request=httpx.Request("GET", "http://localhost:8089"),
        )
        mock_client._client.get.return_value = resp
        with pytest.raises(KGError, match="Single source shortest path failed"):
            await mock_client.traverser_single_source_shortest_path("1:a")


# ---------------------------------------------------------------------------
# Rays — error cases
# ---------------------------------------------------------------------------


class TestRaysErrors:
    @pytest.mark.asyncio()
    async def test_http_error(self, mock_client: HugeGraphClient) -> None:
        mock_client._client.get.side_effect = httpx.ConnectError("conn refused")
        with pytest.raises(KGError):
            await mock_client.traverser_rays("1:a")

    @pytest.mark.asyncio()
    async def test_non_200_status(self, mock_client: HugeGraphClient) -> None:
        resp = httpx.Response(
            status_code=500, json={"error": "server"},
            request=httpx.Request("GET", "http://localhost:8089"),
        )
        mock_client._client.get.return_value = resp
        with pytest.raises(KGError, match="Rays traversal failed"):
            await mock_client.traverser_rays("1:a")


# ---------------------------------------------------------------------------
# Rings — error cases
# ---------------------------------------------------------------------------


class TestRingsErrors:
    @pytest.mark.asyncio()
    async def test_http_error(self, mock_client: HugeGraphClient) -> None:
        mock_client._client.get.side_effect = httpx.ConnectError("broken pipe")
        with pytest.raises(KGError):
            await mock_client.traverser_rings("1:a")

    @pytest.mark.asyncio()
    async def test_non_200_status(self, mock_client: HugeGraphClient) -> None:
        resp = httpx.Response(
            status_code=422, json={"error": "unprocessable"},
            request=httpx.Request("GET", "http://localhost:8089"),
        )
        mock_client._client.get.return_value = resp
        with pytest.raises(KGError, match="Rings traversal failed"):
            await mock_client.traverser_rings("1:a")


# ---------------------------------------------------------------------------
# Crosspoints — error cases
# ---------------------------------------------------------------------------


class TestCrosspointsErrors:
    @pytest.mark.asyncio()
    async def test_http_error(self, mock_client: HugeGraphClient) -> None:
        mock_client._client.get.side_effect = httpx.ConnectError("dns")
        with pytest.raises(KGError):
            await mock_client.traverser_crosspoints("1:a", "1:b")

    @pytest.mark.asyncio()
    async def test_non_200_status(self, mock_client: HugeGraphClient) -> None:
        resp = httpx.Response(
            status_code=403, json={"error": "forbidden"},
            request=httpx.Request("GET", "http://localhost:8089"),
        )
        mock_client._client.get.return_value = resp
        with pytest.raises(KGError, match="Crosspoints traversal failed"):
            await mock_client.traverser_crosspoints("1:a", "1:b")


# ---------------------------------------------------------------------------
# Customized Paths — error cases
# ---------------------------------------------------------------------------


class TestCustomizedPathsErrors:
    @pytest.mark.asyncio()
    async def test_http_error(self, mock_client: HugeGraphClient) -> None:
        mock_client._client.post.side_effect = httpx.ConnectError("timeout")
        with pytest.raises(KGError):
            await mock_client.traverser_customized_paths("1:a", [{"direction": "OUT"}])

    @pytest.mark.asyncio()
    async def test_non_200_status(self, mock_client: HugeGraphClient) -> None:
        resp = httpx.Response(
            status_code=500, json={"error": "internal"},
            request=httpx.Request("POST", "http://localhost:8089"),
        )
        mock_client._client.post.return_value = resp
        with pytest.raises(KGError, match="Customized paths traversal failed"):
            await mock_client.traverser_customized_paths("1:a", [{"direction": "OUT"}])

    @pytest.mark.asyncio()
    async def test_empty_paths_in_response(self, mock_client: HugeGraphClient) -> None:
        resp = _ok_post({"some_key": []})
        mock_client._client.post.return_value = resp
        result = await mock_client.traverser_customized_paths("1:a", [])
        assert result == []


# ---------------------------------------------------------------------------
# K-neighbor — success and error cases
# ---------------------------------------------------------------------------


class TestKneighbor:
    @pytest.mark.asyncio()
    async def test_success_returns_vertices(self, mock_client: HugeGraphClient) -> None:
        resp = _ok_post({"vertices": [{"id": "1:b", "label": "person"}]})
        mock_client._client.post.return_value = resp
        result = await mock_client.traverser_kneighbor("1:a", depth=2)
        assert len(result) == 1
        assert result[0]["id"] == "1:b"
        mock_client._client.post.assert_called_once()

    @pytest.mark.asyncio()
    async def test_success_returns_batch(self, mock_client: HugeGraphClient) -> None:
        resp = _ok_post({"batch": [{"id": "2:c"}]})
        mock_client._client.post.return_value = resp
        result = await mock_client.traverser_kneighbor("1:a", depth=1)
        assert len(result) == 1
        assert result[0]["id"] == "2:c"

    @pytest.mark.asyncio()
    async def test_success_unknown_key_returns_empty(self, mock_client: HugeGraphClient) -> None:
        resp = _ok_post({"other_key": []})
        mock_client._client.post.return_value = resp
        result = await mock_client.traverser_kneighbor("1:a")
        assert result == []

    @pytest.mark.asyncio()
    async def test_http_error(self, mock_client: HugeGraphClient) -> None:
        mock_client._client.post.side_effect = httpx.ConnectError("refused")
        with pytest.raises(KGError):
            await mock_client.traverser_kneighbor("1:a")

    @pytest.mark.asyncio()
    async def test_non_200_status(self, mock_client: HugeGraphClient) -> None:
        resp = httpx.Response(
            status_code=500, json={"error": "fail"},
            request=httpx.Request("POST", "http://localhost:8089"),
        )
        mock_client._client.post.return_value = resp
        with pytest.raises(KGError, match="K-neighbor traversal failed"):
            await mock_client.traverser_kneighbor("1:a")


# ---------------------------------------------------------------------------
# Shortest Path (POST) — success and error cases
# ---------------------------------------------------------------------------


class TestShortestPathErrors:
    @pytest.mark.asyncio()
    async def test_success(self, mock_client: HugeGraphClient) -> None:
        resp = _ok_post({"paths": [{"objects": ["1:a", "1:b"]}]})
        mock_client._client.post.return_value = resp
        result = await mock_client.traverser_shortest_path("1:a", "1:b")
        assert "paths" in result

    @pytest.mark.asyncio()
    async def test_http_error(self, mock_client: HugeGraphClient) -> None:
        mock_client._client.post.side_effect = httpx.ConnectError("timeout")
        with pytest.raises(KGError):
            await mock_client.traverser_shortest_path("1:a", "1:b")

    @pytest.mark.asyncio()
    async def test_non_200_status(self, mock_client: HugeGraphClient) -> None:
        resp = httpx.Response(
            status_code=400, json={"error": "bad"},
            request=httpx.Request("POST", "http://localhost:8089"),
        )
        mock_client._client.post.return_value = resp
        with pytest.raises(KGError, match="Path traversal failed"):
            await mock_client.traverser_shortest_path("1:a", "1:b")
