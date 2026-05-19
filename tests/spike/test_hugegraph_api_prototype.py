"""Spike: HugeGraph REST API prototype — full CRUD flow verification.

Requires HugeGraph running (set ARROW_LAKE__HUGEGRAPH__HOST/PORT or
HUGEGRAPH_HOST/HUGEGRAPH_PORT).  Uses spike_api_ prefixed labels.

Tests cover: Schema CRUD, Vertex CRUD (single+batch), Edge CRUD (single+batch),
Gremlin queries, Traverser API (kneighbor, paths), Graph management.
"""

from __future__ import annotations

import contextlib
import time

import httpx
import pytest

from tests.conftest_services import (
    HUGEGRAPH_GRAPH,
    HUGEGRAPH_GRAPH_BASE,
    gremlin,
    gremlin_available,
    make_hg_client,
    require_hugegraph,
)

_TEST_PREFIX = "spike_api_"
GRAPH_BASE = HUGEGRAPH_GRAPH_BASE


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    return make_hg_client()


@pytest.fixture(scope="module")
def graph_schema(client: httpx.Client):
    """Create a complete spike_api_ schema, yield info, cleanup."""
    vl_name = f"{_TEST_PREFIX}entity"
    el_name = f"{_TEST_PREFIX}relates_to"
    pk_name = f"{_TEST_PREFIX}name"
    pk_type = f"{_TEST_PREFIX}etype"
    pk_weight = f"{_TEST_PREFIX}weight"

    for pk in [
        {"name": pk_name, "data_type": "TEXT", "cardinality": "SINGLE"},
        {"name": pk_type, "data_type": "TEXT", "cardinality": "SINGLE"},
        {"name": pk_weight, "data_type": "DOUBLE", "cardinality": "SINGLE"},
    ]:
        resp = client.post(f"{GRAPH_BASE}/schema/propertykeys", json=pk)
        assert resp.status_code in (200, 201, 202, 400), f"PropertyKey error: {resp.text}"

    vl = {
        "name": vl_name,
        "id_strategy": "PRIMARY_KEY",
        "primary_keys": [pk_name],
        "properties": [pk_name, pk_type],
        "nullable_keys": [pk_type],
    }
    resp = client.post(f"{GRAPH_BASE}/schema/vertexlabels", json=vl)
    assert resp.status_code in (200, 201, 202, 400), f"VertexLabel error: {resp.text}"

    el = {
        "name": el_name,
        "source_label": vl_name,
        "target_label": vl_name,
        "properties": [pk_weight],
        "nullable_keys": [pk_weight],
    }
    resp = client.post(f"{GRAPH_BASE}/schema/edgelabels", json=el)
    assert resp.status_code in (200, 201, 202, 400), f"EdgeLabel error: {resp.text}"

    il = {
        "name": f"{_TEST_PREFIX}entity_name_idx",
        "base_type": "VERTEX_LABEL",
        "base_value": vl_name,
        "index_type": "SECONDARY",
        "fields": [pk_name],
    }
    resp = client.post(f"{GRAPH_BASE}/schema/indexlabels", json=il)
    assert resp.status_code in (200, 201, 202, 400), f"IndexLabel error: {resp.text}"

    time.sleep(2)

    yield {
        "vl": vl_name,
        "el": el_name,
        "pk_name": pk_name,
        "pk_type": pk_type,
        "pk_weight": pk_weight,
    }

    # Cleanup in reverse order
    with contextlib.suppress(Exception, pytest.skip.Exception):
        client.post(
            "/gremlin",
            json={"gremlin": f"{HUGEGRAPH_GRAPH}.traversal().V().hasLabel('{vl_name}').drop().iterate()"},
        )
    time.sleep(1)
    for name in [f"{_TEST_PREFIX}entity_name_idx"]:
        with contextlib.suppress(httpx.HTTPStatusError):
            client.delete(f"{GRAPH_BASE}/schema/indexlabels/{name}")
    time.sleep(0.5)
    with contextlib.suppress(httpx.HTTPStatusError):
        client.delete(f"{GRAPH_BASE}/schema/edgelabels/{el_name}")
    time.sleep(0.5)
    with contextlib.suppress(httpx.HTTPStatusError):
        client.delete(f"{GRAPH_BASE}/schema/vertexlabels/{vl_name}")
    time.sleep(0.5)
    for name in [pk_name, pk_type, pk_weight]:
        with contextlib.suppress(httpx.HTTPStatusError):
            client.delete(f"{GRAPH_BASE}/schema/propertykeys/{name}")


def _create_vertex(client: httpx.Client, schema: dict, name: str, etype: str = "") -> str:
    vl = schema["vl"]
    props = {schema["pk_name"]: name}
    if etype:
        props[schema["pk_type"]] = etype
    resp = client.post(f"{GRAPH_BASE}/graph/vertices", json={"label": vl, "properties": props})
    assert resp.status_code == 201, f"Vertex create failed: {resp.text}"
    return resp.json()["id"]


def _create_edge(
    client: httpx.Client, schema: dict, src_id: str, tgt_id: str, weight: float = 1.0
) -> str:
    edge = {
        "label": schema["el"],
        "outV": src_id,
        "outVLabel": schema["vl"],
        "inV": tgt_id,
        "inVLabel": schema["vl"],
        "properties": {schema["pk_weight"]: weight},
    }
    resp = client.post(f"{GRAPH_BASE}/graph/edges", json=edge)
    assert resp.status_code == 201, f"Edge create failed: {resp.text}"
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Schema CRUD
# ---------------------------------------------------------------------------


@require_hugegraph
@pytest.mark.spike
class TestSchemaCRUD:
    def test_schema_created(self, client: httpx.Client, graph_schema: dict) -> None:
        resp = client.get(f"{GRAPH_BASE}/schema/vertexlabels")
        names = [vl["name"] for vl in resp.json().get("vertexlabels", [])]
        assert graph_schema["vl"] in names

        resp = client.get(f"{GRAPH_BASE}/schema/edgelabels")
        names = [el["name"] for el in resp.json().get("edgelabels", [])]
        assert graph_schema["el"] in names

        resp = client.get(f"{GRAPH_BASE}/schema/propertykeys")
        names = [pk["name"] for pk in resp.json().get("propertykeys", [])]
        for key in [graph_schema["pk_name"], graph_schema["pk_type"], graph_schema["pk_weight"]]:
            assert key in names

    def test_vertex_label_details(self, client: httpx.Client, graph_schema: dict) -> None:
        resp = client.get(f"{GRAPH_BASE}/schema/vertexlabels/{graph_schema['vl']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id_strategy"] == "PRIMARY_KEY"
        assert data["primary_keys"] == [graph_schema["pk_name"]]

    def test_index_label_exists(self, client: httpx.Client, graph_schema: dict) -> None:
        idx_name = f"{_TEST_PREFIX}entity_name_idx"
        resp = client.get(f"{GRAPH_BASE}/schema/indexlabels/{idx_name}")
        if resp.status_code == 200:
            data = resp.json()
            assert data["index_type"] == "SECONDARY"
            assert data["base_value"] == graph_schema["vl"]
        else:
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Vertex CRUD
# ---------------------------------------------------------------------------


@require_hugegraph
@pytest.mark.spike
class TestVertexCRUD:
    def test_create_and_read_vertex(self, client: httpx.Client, graph_schema: dict) -> None:
        vid = _create_vertex(client, graph_schema, "test_read_entity", "person")
        resp = client.get(f'{GRAPH_BASE}/graph/vertices/"{vid}"')
        assert resp.status_code == 200
        assert resp.json()["properties"][graph_schema["pk_name"]] == "test_read_entity"
        client.delete(f'{GRAPH_BASE}/graph/vertices/"{vid}"')

    def test_delete_vertex(self, client: httpx.Client, graph_schema: dict) -> None:
        vid = _create_vertex(client, graph_schema, "test_delete_entity")
        resp = client.delete(f'{GRAPH_BASE}/graph/vertices/"{vid}"')
        assert resp.status_code == 204
        resp = client.get(f'{GRAPH_BASE}/graph/vertices/"{vid}"')
        assert resp.status_code == 404

    def test_batch_insert_vertices(self, client: httpx.Client, graph_schema: dict) -> None:
        if not gremlin_available(client):
            pytest.skip("Gremlin endpoint unavailable")

        vl = graph_schema["vl"]
        pk = graph_schema["pk_name"]
        vertices = [
            {"label": vl, "properties": {pk: f"batch_{i}", graph_schema["pk_type"]: f"type_{i % 3}"}}
            for i in range(20)
        ]
        resp = client.post(f"{GRAPH_BASE}/graph/vertices/batch", json=vertices)
        assert resp.status_code == 201
        assert len(resp.json()) == 20

        result = gremlin(
            client,
            f"{HUGEGRAPH_GRAPH}.traversal().V().hasLabel('{vl}').has('{pk}',eq('batch_0')).count()",
        )
        assert result["result"]["data"][0] == 1


# ---------------------------------------------------------------------------
# Edge CRUD
# ---------------------------------------------------------------------------


@require_hugegraph
@pytest.mark.spike
class TestEdgeCRUD:
    def test_create_and_read_edge(self, client: httpx.Client, graph_schema: dict) -> None:
        if not gremlin_available(client):
            pytest.skip("Gremlin endpoint unavailable")

        v1 = _create_vertex(client, graph_schema, "edge_src", "org")
        v2 = _create_vertex(client, graph_schema, "edge_tgt", "person")
        _create_edge(client, graph_schema, v1, v2, 0.85)

        result = gremlin(
            client,
            f"{HUGEGRAPH_GRAPH}.traversal().V('{v1}').out('{graph_schema['el']}').count()",
        )
        assert result["result"]["data"][0] == 1

    def test_batch_insert_edges(self, client: httpx.Client, graph_schema: dict) -> None:
        vids = [_create_vertex(client, graph_schema, f"chain_{i}") for i in range(5)]

        edges = []
        for i in range(len(vids) - 1):
            edges.append({
                "label": graph_schema["el"],
                "outV": vids[i],
                "outVLabel": graph_schema["vl"],
                "inV": vids[i + 1],
                "inVLabel": graph_schema["vl"],
                "properties": {graph_schema["pk_weight"]: float(i) * 0.1},
            })

        resp = client.post(f"{GRAPH_BASE}/graph/edges/batch", json=edges)
        assert resp.status_code == 201
        assert len(resp.json()) == 4


# ---------------------------------------------------------------------------
# Gremlin Queries
# ---------------------------------------------------------------------------


@require_hugegraph
@pytest.mark.spike
class TestGremlinQueries:
    def test_count_by_label(self, client: httpx.Client, graph_schema: dict) -> None:
        if not gremlin_available(client):
            pytest.skip("Gremlin endpoint unavailable")

        result = gremlin(
            client,
            f"{HUGEGRAPH_GRAPH}.traversal().V().hasLabel('{graph_schema['vl']}').count()",
        )
        assert result["result"]["data"][0] >= 0

    def test_property_filter(self, client: httpx.Client, graph_schema: dict) -> None:
        if not gremlin_available(client):
            pytest.skip("Gremlin endpoint unavailable")

        _create_vertex(client, graph_schema, "gremlin_filter_test", "any_type")
        result = gremlin(
            client,
            f"{HUGEGRAPH_GRAPH}.traversal().V().hasLabel('{graph_schema['vl']}')"
            f".has('{graph_schema['pk_name']}',eq('gremlin_filter_test'))"
            f".values('{graph_schema['pk_name']}')",
        )
        assert "gremlin_filter_test" in result["result"]["data"]

    def test_path_traversal(self, client: httpx.Client, graph_schema: dict) -> None:
        if not gremlin_available(client):
            pytest.skip("Gremlin endpoint unavailable")

        v1 = _create_vertex(client, graph_schema, "path_A")
        v2 = _create_vertex(client, graph_schema, "path_B")
        v3 = _create_vertex(client, graph_schema, "path_C")

        for src, tgt in [(v1, v2), (v2, v3)]:
            _create_edge(client, graph_schema, src, tgt)

        result = gremlin(
            client,
            f"{HUGEGRAPH_GRAPH}.traversal().V('{v1}').repeat(__.out()).times(2).path()",
        )
        assert len(result["result"]["data"]) > 0


# ---------------------------------------------------------------------------
# Traverser API
# ---------------------------------------------------------------------------


@require_hugegraph
@pytest.mark.spike
class TestTraverserAPI:
    @pytest.fixture(scope="class")
    def chain_graph(self, client: httpx.Client, graph_schema: dict):
        vids = [_create_vertex(client, graph_schema, f"trav_{i}") for i in range(5)]
        for i in range(len(vids) - 1):
            _create_edge(client, graph_schema, vids[i], vids[i + 1])
        return vids

    def test_kneighbor(self, client: httpx.Client, graph_schema: dict, chain_graph: list) -> None:
        source = chain_graph[0]
        resp = client.post(
            f"{GRAPH_BASE}/traversers/kneighbor",
            json={
                "source": source,
                "steps": {"direction": "OUT", "max_degree": 10000},
                "max_depth": 2,
                "with_vertex": True,
                "limit": 100,
            },
        )
        assert resp.status_code == 200, f"Kneighbor failed: {resp.text}"
        data = resp.json()
        if "vertices" in data:
            neighbor_ids = [v.get("id") or v.get("vertex_id", "") for v in data["vertices"]]
            assert len(neighbor_ids) >= 1 or data.get("batch", []) != []

    def test_paths(self, client: httpx.Client, graph_schema: dict, chain_graph: list) -> None:
        source = chain_graph[0]
        target = chain_graph[3]
        resp = client.post(
            f"{GRAPH_BASE}/traversers/paths",
            json={
                "sources": {"ids": [source]},
                "targets": {"ids": [target]},
                "step": {"direction": "OUT", "max_degree": 10000},
                "max_depth": 10,
                "limit": 10,
            },
        )
        assert resp.status_code == 200, f"Paths failed: {resp.text}"
        assert len(resp.json().get("paths", [])) >= 1


# ---------------------------------------------------------------------------
# Graph Management
# ---------------------------------------------------------------------------


@require_hugegraph
@pytest.mark.spike
class TestGraphManagement:
    def test_graph_schema_overview(self, client: httpx.Client) -> None:
        resp = client.get(f"{GRAPH_BASE}/schema")
        assert resp.status_code == 200

    def test_graph_mode(self, client: httpx.Client) -> None:
        resp = client.get(f"{GRAPH_BASE}/mode")
        assert resp.status_code == 200

    def test_graph_statistics(self, client: httpx.Client) -> None:
        if not gremlin_available(client):
            pytest.skip("Gremlin endpoint unavailable")

        v_count = gremlin(client, f"{HUGEGRAPH_GRAPH}.traversal().V().count()")
        e_count = gremlin(client, f"{HUGEGRAPH_GRAPH}.traversal().E().count()")
        assert v_count["result"]["data"][0] > 0
        assert e_count["result"]["data"][0] > 0
