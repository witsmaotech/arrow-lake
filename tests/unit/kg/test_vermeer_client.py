"""Tests for HugeGraph-Vermeer OLAP algorithm client."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from arrow_lake.config import HugeGraphConfig
from arrow_lake.exceptions import KGError
from arrow_lake.knowledge_graph.vermeer_client import VermeerClient


@pytest.fixture()
def config() -> HugeGraphConfig:
    return HugeGraphConfig(
        enabled=True, host="localhost", port=8089,
        graph_name="test_graph", timeout_seconds=10.0,
        vermeer_host="localhost", vermeer_port=8081,
    )


@pytest.fixture()
def mock_client(config: HugeGraphConfig) -> VermeerClient:
    client = VermeerClient(config)
    client._client = AsyncMock(spec=httpx.AsyncClient)
    return client


def _ok(json_data: dict) -> httpx.Response:
    return httpx.Response(status_code=200, json=json_data, request=httpx.Request("POST", "http://localhost:8081"))


def _created(json_data: dict) -> httpx.Response:
    return httpx.Response(status_code=201, json=json_data, request=httpx.Request("POST", "http://localhost:8081"))


# ---------------------------------------------------------------------------
# Job Management
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_submit_job(mock_client: VermeerClient) -> None:
    mock_client._client.post.return_value = _created({"job_id": "job-123"})
    job_id = await mock_client.submit_job("pagerank", "test_graph", iterations=20)
    assert job_id == "job-123"


@pytest.mark.asyncio()
async def test_job_status(mock_client: VermeerClient) -> None:
    mock_client._client.get.return_value = _ok({"job_id": "job-123", "task_status": "running", "progress": 50})
    status = await mock_client.job_status("job-123")
    assert status["task_status"] == "running"


@pytest.mark.asyncio()
async def test_job_results(mock_client: VermeerClient) -> None:
    mock_client._client.get.return_value = _ok({"ranks": [{"vertex_id": "v1", "rank": 0.9}]})
    results = await mock_client.job_results("job-123")
    assert len(results["ranks"]) == 1


@pytest.mark.asyncio()
async def test_cancel_job(mock_client: VermeerClient) -> None:
    mock_client._client.delete.return_value = httpx.Response(status_code=200, json={}, request=httpx.Request("DELETE", "http://localhost:8081"))
    await mock_client.cancel_job("job-123")


@pytest.mark.asyncio()
async def test_submit_job_non_200_raises(mock_client: VermeerClient) -> None:
    resp = httpx.Response(status_code=500, json={"error": "fail"}, request=httpx.Request("POST", "http://localhost:8081"))
    mock_client._client.post.return_value = resp
    with pytest.raises(KGError):
        await mock_client.submit_job("pagerank", "test_graph")


# ---------------------------------------------------------------------------
# Wait for job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_wait_for_job_completed(mock_client: VermeerClient) -> None:
    mock_client._client.get.side_effect = [
        _ok({"task_status": "running", "progress": 50}),
        _ok({"task_status": "completed", "progress": 100}),
        _ok({"ranks": [{"vertex_id": "v1", "rank": 0.9}]}),
    ]
    result = await mock_client._wait_for_job("job-123")
    assert "ranks" in result


@pytest.mark.asyncio()
async def test_wait_for_job_failed(mock_client: VermeerClient) -> None:
    mock_client._client.get.return_value = _ok({"task_status": "failed", "error": "OOM"})
    with pytest.raises(KGError):
        await mock_client._wait_for_job("job-123")


# ---------------------------------------------------------------------------
# High-level algorithm methods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_pagerank(mock_client: VermeerClient) -> None:
    mock_client._client.post.return_value = _created({"job_id": "jr-1"})
    mock_client._client.get.side_effect = [
        _ok({"task_status": "completed"}),
        _ok({"ranks": [{"vertex_id": "v1", "rank": 0.8}]}),
    ]
    result = await mock_client.pagerank()
    assert "ranks" in result


@pytest.mark.asyncio()
async def test_louvain(mock_client: VermeerClient) -> None:
    mock_client._client.post.return_value = _created({"job_id": "jr-2"})
    mock_client._client.get.side_effect = [
        _ok({"task_status": "completed"}),
        _ok({"communities": [{"id": "c1", "vertices": ["v1", "v2"]}]}),
    ]
    result = await mock_client.louvain(resolution=1.0)
    assert "communities" in result


@pytest.mark.asyncio()
async def test_wcc(mock_client: VermeerClient) -> None:
    mock_client._client.post.return_value = _created({"job_id": "jr-3"})
    mock_client._client.get.side_effect = [
        _ok({"task_status": "completed"}),
        _ok({"components": [{"id": "c1", "vertices": ["v1"]}]}),
    ]
    result = await mock_client.wcc()
    assert "components" in result


@pytest.mark.asyncio()
async def test_triangle_count(mock_client: VermeerClient) -> None:
    mock_client._client.post.return_value = _created({"job_id": "jr-4"})
    mock_client._client.get.side_effect = [
        _ok({"task_status": "completed"}),
        _ok({"triangle_count": 42}),
    ]
    result = await mock_client.triangle_count()
    assert result["triangle_count"] == 42


@pytest.mark.asyncio()
async def test_k_core(mock_client: VermeerClient) -> None:
    mock_client._client.post.return_value = _created({"job_id": "jr-5"})
    mock_client._client.get.side_effect = [
        _ok({"task_status": "completed"}),
        _ok({"core_vertices": ["v1", "v2"], "k": 3}),
    ]
    result = await mock_client.k_core(k=3)
    assert result["k"] == 3


@pytest.mark.asyncio()
async def test_degree_centrality(mock_client: VermeerClient) -> None:
    mock_client._client.post.return_value = _created({"job_id": "jr-6"})
    mock_client._client.get.side_effect = [
        _ok({"task_status": "completed"}),
        _ok({"ranks": [{"vertex_id": "v1", "degree_centrality": 0.5}]}),
    ]
    result = await mock_client.degree_centrality()
    assert "ranks" in result


@pytest.mark.asyncio()
async def test_closeness_centrality(mock_client: VermeerClient) -> None:
    mock_client._client.post.return_value = _created({"job_id": "jr-7"})
    mock_client._client.get.side_effect = [
        _ok({"task_status": "completed"}),
        _ok({"ranks": [{"vertex_id": "v1", "closeness": 0.6}]}),
    ]
    result = await mock_client.closeness_centrality()
    assert "ranks" in result


@pytest.mark.asyncio()
async def test_betweenness_centrality(mock_client: VermeerClient) -> None:
    mock_client._client.post.return_value = _created({"job_id": "jr-8"})
    mock_client._client.get.side_effect = [
        _ok({"task_status": "completed"}),
        _ok({"ranks": [{"vertex_id": "v1", "betweenness": 0.3}]}),
    ]
    result = await mock_client.betweenness_centrality()
    assert "ranks" in result


@pytest.mark.asyncio()
async def test_label_propagation(mock_client: VermeerClient) -> None:
    mock_client._client.post.return_value = _created({"job_id": "jr-9"})
    mock_client._client.get.side_effect = [
        _ok({"task_status": "completed"}),
        _ok({"communities": []}),
    ]
    result = await mock_client.label_propagation()
    assert "communities" in result


# ---------------------------------------------------------------------------
# HTTP error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_http_error_raises_kg_error(mock_client: VermeerClient) -> None:
    mock_client._client.post.side_effect = httpx.ConnectError("refused")
    with pytest.raises(KGError):
        await mock_client.submit_job("pagerank", "test_graph")
