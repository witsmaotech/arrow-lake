"""Unit tests for HugeGraph REST client (mock httpx)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from arrow_lake.config import HugeGraphConfig
from arrow_lake.exceptions import ErrorCode, KGError
from arrow_lake.knowledge_graph.client import HugeGraphClient


@pytest.fixture
def config() -> HugeGraphConfig:
    return HugeGraphConfig(
        enabled=True,
        host="localhost",
        port=8089,
        graph_name="test_graph",
        timeout_seconds=10.0,
    )


@pytest.fixture
def mock_client(config: HugeGraphConfig) -> HugeGraphClient:
    """Create a HugeGraphClient with mocked httpx transport."""
    client = HugeGraphClient(config)
    # Replace the internal httpx client with a mock
    client._client = AsyncMock(spec=httpx.AsyncClient)
    return client


def _mock_response(status_code: int, json_data: dict) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("POST", "http://localhost:8089"),
    )


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_success(mock_client: HugeGraphClient) -> None:
    mock_client._client.get.return_value = _mock_response(200, {"version": "1.7.0"})
    result = await mock_client.ping()
    assert result is True
    mock_client._client.get.assert_called_once_with("/versions")


@pytest.mark.asyncio
async def test_ping_failure(mock_client: HugeGraphClient) -> None:
    mock_client._client.get.side_effect = httpx.ConnectError("connection refused")
    result = await mock_client.ping()
    assert result is False


# ---------------------------------------------------------------------------
# gremlin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gremlin_query(mock_client: HugeGraphClient) -> None:
    mock_client._client.post.return_value = _mock_response(200, {
        "requestId": "1",
        "status": {"code": 200},
        "result": {"data": [42], "meta": {}},
    })
    result = await mock_client.gremlin("test_graph.traversal().V().count()")
    assert result == [42]


@pytest.mark.asyncio
async def test_gremlin_error(mock_client: HugeGraphClient) -> None:
    mock_client._client.post.return_value = _mock_response(200, {
        "requestId": "1",
        "status": {"code": 500, "message": "Internal error"},
        "result": {"data": [], "meta": {}},
    })
    with pytest.raises(KGError) as exc_info:
        await mock_client.gremlin("invalid query")
    assert exc_info.value.error_code == ErrorCode.KG_QUERY_FAILED


# ---------------------------------------------------------------------------
# add_vertices (batch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_vertices(mock_client: HugeGraphClient) -> None:
    vertices = [
        {"label": "person", "properties": {"name": "Alice"}},
        {"label": "person", "properties": {"name": "Bob"}},
    ]
    mock_client._client.post.return_value = _mock_response(201, ["id1", "id2"])
    ids = await mock_client.add_vertices(vertices)
    assert ids == ["id1", "id2"]
    call_args = mock_client._client.post.call_args
    assert "/graph/vertices/batch" in call_args[0][0]


# ---------------------------------------------------------------------------
# add_edges (batch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_edges(mock_client: HugeGraphClient) -> None:
    edges = [
        {
            "label": "knows",
            "outV": "1:Alice",
            "outVLabel": "person",
            "inV": "2:Bob",
            "inVLabel": "person",
            "properties": {},
        },
    ]
    mock_client._client.post.return_value = _mock_response(201, ["edge1"])
    count = await mock_client.add_edges(edges)
    assert count == 1
    call_args = mock_client._client.post.call_args
    assert "/graph/edges/batch" in call_args[0][0]


# ---------------------------------------------------------------------------
# traverser_kneighbor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kneighbor(mock_client: HugeGraphClient) -> None:
    mock_client._client.post.return_value = _mock_response(200, {
        "vertices": [{"id": "1:Alice"}, {"id": "2:Bob"}],
    })
    result = await mock_client.traverser_kneighbor(source="1:Alice", depth=2)
    assert len(result) == 2
    call_args = mock_client._client.post.call_args
    assert "/traversers/kneighbor" in call_args[0][0]
    body = call_args[1]["json"]
    assert body["source"] == "1:Alice"
    assert body["max_depth"] == 2
    assert body["steps"]["direction"] == "OUT"


# ---------------------------------------------------------------------------
# traverser_shortest_path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shortest_path(mock_client: HugeGraphClient) -> None:
    mock_client._client.post.return_value = _mock_response(200, {
        "paths": [{"vertices": ["1:A", "2:B", "3:C"]}],
    })
    result = await mock_client.traverser_shortest_path(source="1:A", target="3:C")
    assert "paths" in result
    call_args = mock_client._client.post.call_args
    assert "/traversers/paths" in call_args[0][0]
    body = call_args[1]["json"]
    assert body["sources"]["ids"] == ["1:A"]
    assert body["targets"]["ids"] == ["3:C"]


# ---------------------------------------------------------------------------
# get_vertex
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_vertex(mock_client: HugeGraphClient) -> None:
    mock_client._client.get.return_value = _mock_response(200, {
        "id": "20001:marko",
        "label": "person",
        "properties": {"name": "marko", "age": 29},
    })
    result = await mock_client.get_vertex("20001:marko")
    assert result is not None
    assert result["properties"]["name"] == "marko"


@pytest.mark.asyncio
async def test_get_vertex_not_found(mock_client: HugeGraphClient) -> None:
    mock_client._client.get.return_value = httpx.Response(
        status_code=404,
        json={},
        request=httpx.Request("GET", "http://localhost:8089"),
    )
    result = await mock_client.get_vertex("nonexistent")
    assert result is None


# ---------------------------------------------------------------------------
# get_schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_schema(mock_client: HugeGraphClient) -> None:
    mock_client._client.get.return_value = _mock_response(200, {
        "vertexlabels": [{"name": "person"}],
        "edgelabels": [{"name": "knows"}],
    })
    result = await mock_client.get_schema()
    assert "vertexlabels" in result


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_stats(mock_client: HugeGraphClient) -> None:
    mock_client._client.get.side_effect = [
        _mock_response(200, {"total": 100}),
        _mock_response(200, {"total": 200}),
    ]
    stats = await mock_client.get_stats()
    assert stats["total_vertices"] == 100
    assert stats["total_edges"] == 200


# ---------------------------------------------------------------------------
# ensure_schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_schema(mock_client: HugeGraphClient) -> None:
    """Verify ensure_schema calls POST for each schema element."""
    # ensure_graph() first calls GET /graphs → graph already exists
    mock_client._client.get.return_value = _mock_response(200, {"graphs": ["test_graph"]})
    # Then schema POSTs (2 PK + 1 VL + 1 EL + 1 IL = 5)
    mock_client._client.post.side_effect = [
        _mock_response(201, {"id": "pk_name"}),
        _mock_response(201, {"id": "pk_type"}),
        _mock_response(201, {"id": "vl"}),
        _mock_response(201, {"id": "el"}),
        _mock_response(202, {"id": "il"}),
    ]

    schema = {
        "property_keys": [
            {"name": "name", "data_type": "TEXT", "cardinality": "SINGLE"},
            {"name": "type", "data_type": "TEXT", "cardinality": "SINGLE"},
        ],
        "vertex_labels": [
            {"name": "entity", "id_strategy": "PRIMARY_KEY",
             "primary_keys": ["name"], "properties": ["name", "type"]},
        ],
        "edge_labels": [
            {"name": "related_to", "source_label": "entity",
             "target_label": "entity"},
        ],
        "index_labels": [
            {"name": "entity_name_idx", "base_type": "VERTEX_LABEL",
             "base_value": "entity", "index_type": "SECONDARY", "fields": ["name"]},
        ],
    }
    await mock_client.ensure_schema(schema)

    assert mock_client._client.post.call_count == 5


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close(mock_client: HugeGraphClient) -> None:
    await mock_client.close()
    mock_client._client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_error_raises_kg_error(mock_client: HugeGraphClient) -> None:
    mock_client._client.post.side_effect = httpx.ConnectError("refused")
    with pytest.raises(KGError) as exc_info:
        await mock_client.gremlin("test.traversal().V()")
    assert exc_info.value.error_code == ErrorCode.KG_CONNECTION_FAILED
