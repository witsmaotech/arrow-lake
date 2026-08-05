"""Unit tests for HugeGraph REST client (mock httpx)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from arrow_lake.config import HugeGraphConfig
from arrow_lake.exceptions import ErrorCode, KGError
from arrow_lake.knowledge_graph.client import HugeGraphClient, _DEFAULT_MAX_RETRIES


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
    mock_client._client.get.assert_called_once_with("/versions", params=None)


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
# add_vertices / add_edges with update_strategies (v1.10.2 P3.2)
#
# When update_strategies is provided the client must PUT the batch endpoint
# with a {"<vertices|edges>":[...], "update_strategies":{...}} wrapper so
# HugeGraph applies per-property merge (e.g. source_chunk UNION). Without it the
# default create/upsert POST path is used (no regression).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_vertices_with_update_strategies_uses_put(mock_client: HugeGraphClient) -> None:
    """update_strategies ⇒ PUT /graph/vertices/batch with wrapper, ids parsed
    from the {"vertices":[{"id":...}]} response."""
    vertices = [{"label": "entity", "properties": {"name": "A"}}]
    # PUT-with-wrapper returns full vertex objects under "vertices"
    mock_client._client.put.return_value = _mock_response(
        200, {"vertices": [{"id": "1:A", "label": "entity"}]},
    )
    ids = await mock_client.add_vertices(
        vertices, update_strategies={"source_chunk": "UNION"},
    )
    assert ids == ["1:A"]
    mock_client._client.post.assert_not_called()
    call_args = mock_client._client.put.call_args
    assert "/graph/vertices/batch" in call_args[0][0]
    body = call_args[1]["json"]
    assert body["vertices"] == vertices
    assert body["update_strategies"] == {"source_chunk": "UNION"}


@pytest.mark.asyncio
async def test_add_vertices_without_strategies_uses_post(mock_client: HugeGraphClient) -> None:
    """No update_strategies ⇒ default POST bare list (regression guard)."""
    vertices = [{"label": "entity", "properties": {"name": "A"}}]
    mock_client._client.post.return_value = _mock_response(201, ["1:A"])
    ids = await mock_client.add_vertices(vertices)
    assert ids == ["1:A"]
    mock_client._client.put.assert_not_called()
    # bare list posted (no wrapper)
    assert mock_client._client.post.call_args[1]["json"] == vertices


@pytest.mark.asyncio
async def test_add_edges_with_update_strategies_uses_put(mock_client: HugeGraphClient) -> None:
    """update_strategies ⇒ PUT /graph/edges/batch with {"edges":[...],...}."""
    edges = [{
        "label": "related_to", "outV": "1:A", "outVLabel": "entity",
        "inV": "2:B", "inVLabel": "entity",
        "properties": {"relation_type": "uses"},
    }]
    mock_client._client.put.return_value = _mock_response(
        200, {"edges": [{"id": "e1"}, {"id": "e2"}]},
    )
    count = await mock_client.add_edges(
        edges, update_strategies={"weight": "SUM"},
    )
    assert count == 2
    mock_client._client.post.assert_not_called()
    call_args = mock_client._client.put.call_args
    assert "/graph/edges/batch" in call_args[0][0]
    body = call_args[1]["json"]
    assert body["edges"] == edges
    assert body["update_strategies"] == {"weight": "SUM"}


@pytest.mark.asyncio
async def test_add_edges_without_strategies_uses_post(mock_client: HugeGraphClient) -> None:
    """No update_strategies ⇒ default POST bare list (regression guard)."""
    edges = [{"label": "knows", "outV": "1:A", "outVLabel": "person",
              "inV": "2:B", "inVLabel": "person", "properties": {}}]
    mock_client._client.post.return_value = _mock_response(201, ["e1"])
    count = await mock_client.add_edges(edges)
    assert count == 1
    mock_client._client.put.assert_not_called()
    assert mock_client._client.post.call_args[1]["json"] == edges


# ---------------------------------------------------------------------------
# 5xx server errors: exponential backoff retry (P0.1)
#
# HugeGraph returns 5xx with "too busy to write" when rocksdb write throughput
# saturates. Such responses must be retried with backoff (not crash the build),
# while genuine 4xx client errors must NOT be retried.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_vertices_retries_on_server_error(mock_client: HugeGraphClient) -> None:
    """A transient 5xx (e.g. 'too busy to write') is retried, then succeeds."""
    vertices = [{"label": "person", "properties": {"name": "Alice"}}]
    mock_client._client.post.side_effect = [
        _mock_response(503, {}),  # "too busy to write" → retry
        _mock_response(201, ["id1"]),  # success on retry
    ]
    ids = await mock_client.add_vertices(vertices)
    assert ids == ["id1"]
    assert mock_client._client.post.call_count == 2


@pytest.mark.asyncio
async def test_add_vertices_raises_after_retries_exhausted(
    mock_client: HugeGraphClient,
) -> None:
    """Persistent 5xx exhausts retries, then surfaces as KGError(KG_CONNECTION_FAILED)."""
    vertices = [{"label": "person", "properties": {"name": "Alice"}}]
    mock_client._client.post.return_value = _mock_response(503, {})
    with pytest.raises(KGError) as exc_info:
        await mock_client.add_vertices(vertices)
    assert exc_info.value.error_code == ErrorCode.KG_CONNECTION_FAILED
    assert mock_client._client.post.call_count == _DEFAULT_MAX_RETRIES  # initial + 2 retries


@pytest.mark.asyncio
async def test_add_edges_retries_on_server_error(mock_client: HugeGraphClient) -> None:
    """add_edges also retries transient 5xx, then succeeds."""
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
    mock_client._client.post.side_effect = [
        _mock_response(500, {}),
        _mock_response(201, ["edge1"]),
    ]
    count = await mock_client.add_edges(edges)
    assert count == 1
    assert mock_client._client.post.call_count == 2


@pytest.mark.asyncio
async def test_client_error_not_retried(mock_client: HugeGraphClient) -> None:
    """4xx is NOT retried (only 5xx); surfaces the add_vertices status check immediately."""
    vertices = [{"label": "person", "properties": {"name": "Alice"}}]
    mock_client._client.post.return_value = _mock_response(400, {})
    with pytest.raises(KGError) as exc_info:
        await mock_client.add_vertices(vertices)
    assert exc_info.value.error_code == ErrorCode.KG_BUILD_FAILED
    assert mock_client._client.post.call_count == 1  # no retry


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
    # First GET: list_graphs (graph_exists guard) → graph present
    # Then vertices + edges counts
    mock_client._client.get.side_effect = [
        _mock_response(200, {"graphs": ["test_graph"]}),
        _mock_response(200, {"vertices": [{"id": "1:a"}, {"id": "1:b"}]}),
        _mock_response(200, {"edges": [{"id": "S1>1>>S2"}]}),
    ]
    stats = await mock_client.get_stats()
    assert stats["total_vertices"] == 2
    assert stats["total_edges"] == 1


@pytest.mark.asyncio
async def test_get_stats_short_circuits_when_graph_missing(
    mock_client: HugeGraphClient,
) -> None:
    """Non-existent graph → {0,0} without hitting vertices/edges endpoints.

    Regression guard for slow dataset-detail pages: a 5xx on the vertices
    endpoint used to trigger tenacity retry + backoff (~seconds) for every
    KG-less dataset. The existence check must short-circuit before any
    vertex/edge fetch.
    """
    # list_graphs returns graphs that do NOT include our target
    mock_client._client.get.return_value = _mock_response(
        200, {"graphs": ["other_graph"]}
    )
    stats = await mock_client.get_stats(graph_name="kg_noaa_china")
    assert stats == {"total_vertices": 0, "total_edges": 0}
    # Only the list_graphs call happened — no vertices/edges fetch
    assert mock_client._client.get.await_count == 1
    first_path = mock_client._client.get.call_args[0][0]
    assert first_path == "/graphs"


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


# ---------------------------------------------------------------------------
# Per-dataset graph isolation (v1.8.6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_vertices_by_property_returns_matches(
    mock_client: HugeGraphClient,
) -> None:
    # Arrange
    mock_client._client.get.return_value = _mock_response(
        200, {"vertices": [{"id": "3:Python", "label": "entity"}]}
    )
    # Act
    result = await mock_client.find_vertices_by_property(
        "entity", {"name": "Python"}, graph_name="kg_ds_a"
    )
    # Assert — hit the per-dataset graph URL with label + properties params
    mock_client._client.get.assert_called_once()
    call = mock_client._client.get.call_args
    assert call.args[0] == "/graphs/kg_ds_a/graph/vertices"
    assert call.kwargs["params"]["label"] == "entity"
    assert '"name": "Python"' in call.kwargs["params"]["properties"]
    assert result == [{"id": "3:Python", "label": "entity"}]


@pytest.mark.asyncio
async def test_find_vertices_by_property_404_returns_empty(
    mock_client: HugeGraphClient,
) -> None:
    # Arrange — unbuilt dataset graph → 404 → empty (not an error)
    mock_client._client.get.return_value = _mock_response(404, {})
    # Act
    result = await mock_client.find_vertices_by_property(
        "entity", {"name": "X"}, graph_name="kg_missing"
    )
    # Assert
    assert result == []


@pytest.mark.asyncio
async def test_drop_graph_success(mock_client: HugeGraphClient) -> None:
    # Arrange
    mock_client._client.delete.return_value = _mock_response(204, {})
    # Act
    ok = await mock_client.drop_graph("kg_ds_a")
    # Assert — DELETE with drop confirm message
    mock_client._client.delete.assert_called_once()
    url = mock_client._client.delete.call_args.args[0]
    assert url.startswith("/graphspaces/DEFAULT/graphs/kg_ds_a")
    assert "drop+the+graph" in url
    assert ok is True


@pytest.mark.asyncio
async def test_drop_graph_failure_raises(mock_client: HugeGraphClient) -> None:
    # Arrange — non-success status
    mock_client._client.delete.return_value = _mock_response(400, {})
    # Act / Assert
    with pytest.raises(KGError):
        await mock_client.drop_graph("kg_ds_a")


@pytest.mark.asyncio
async def test_add_vertices_uses_overridden_graph_name(
    mock_client: HugeGraphClient,
) -> None:
    # Arrange — per-dataset graph_name must land in the URL, not the config default
    mock_client._client.post.return_value = _mock_response(201, ["1:ok"])
    # Act
    await mock_client.add_vertices(
        [{"label": "entity", "properties": {"name": "x"}}], graph_name="kg_ds_a"
    )
    # Assert
    url = mock_client._client.post.call_args.args[0]
    assert url == "/graphs/kg_ds_a/graph/vertices/batch"


@pytest.mark.asyncio
async def test_ensure_graph_uses_overridden_name(mock_client: HugeGraphClient) -> None:
    # Arrange — list_graphs empty → create with the overridden name
    mock_client._client.get.return_value = _mock_response(200, {"graphs": []})
    mock_client._client.post.return_value = _mock_response(201, {})
    # Act
    ok = await mock_client.ensure_graph(graph_name="kg_ds_a")
    # Assert — creation POST targets kg_ds_a and store field uses it
    create_call = mock_client._client.post.call_args
    assert "/graphspaces/DEFAULT/graphs/kg_ds_a" in create_call.args[0]
    assert create_call.kwargs["json"]["store"] == "kg_ds_a"
    assert ok is True

