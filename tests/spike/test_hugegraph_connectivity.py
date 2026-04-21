"""Spike: HugeGraph Docker connectivity and API verification.

Requires HugeGraph running on localhost:8089 (or set HUGEGRAPH_HOST/HUGEGRAPH_PORT).

Marked with pytest.mark.spike to exclude from regular test runs.

IMPORTANT: These tests share the existing 'hugegraph' graph. CRUD tests use
spike_test_ prefixed labels to avoid interfering with existing monitoring data.
Never call /clear on the shared graph.
"""

from __future__ import annotations

import contextlib
import os
import time

import httpx
import pytest

HUGEGRAPH_HOST = os.getenv("HUGEGRAPH_HOST", "localhost")
HUGEGRAPH_PORT = int(os.getenv("HUGEGRAPH_PORT", "8089"))
HUGEGRAPH_GRAPH = os.getenv("HUGEGRAPH_GRAPH", "hugegraph")
BASE_URL = f"http://{HUGEGRAPH_HOST}:{HUGEGRAPH_PORT}"
GRAPH_BASE = f"/graphs/{HUGEGRAPH_GRAPH}"

# Unique prefix to isolate spike test data from existing monitoring data
_TEST_PREFIX = "spike_test_"


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    """Shared httpx sync client for all tests."""
    return httpx.Client(base_url=BASE_URL, timeout=30.0)


def _gremlin(client: httpx.Client, query: str) -> dict:
    """Execute a Gremlin query via the /gremlin endpoint.

    HugeGraph 1.7.0 uses the graph name as the traversal source variable.
    Endpoint: POST /gremlin (not /graphs/{name}/gremlin)
    Traversal: {graph_name}.traversal().V() (not g.V())
    """
    resp = client.post("/gremlin", json={"gremlin": query})
    assert resp.status_code == 200, f"Gremlin query failed: {resp.text}"
    return resp.json()


@pytest.fixture(scope="module")
def test_labels(client: httpx.Client):
    """Create spike_test_ prefixed schema, yield label names, cleanup after module."""
    labels = {
        "vl": f"{_TEST_PREFIX}person",
        "el": f"{_TEST_PREFIX}knows",
    }

    # Create PropertyKeys
    for pk in [
        {"name": f"{_TEST_PREFIX}name", "data_type": "TEXT", "cardinality": "SINGLE"},
        {"name": f"{_TEST_PREFIX}age", "data_type": "INT", "cardinality": "SINGLE"},
    ]:
        resp = client.post(f"{GRAPH_BASE}/schema/propertykeys", json=pk)
        assert resp.status_code in (200, 201, 400), f"PropertyKey error: {resp.text}"

    # Create VertexLabel
    vl = {
        "name": labels["vl"],
        "id_strategy": "PRIMARY_KEY",
        "primary_keys": [f"{_TEST_PREFIX}name"],
        "properties": [f"{_TEST_PREFIX}name", f"{_TEST_PREFIX}age"],
        "nullable_keys": [f"{_TEST_PREFIX}age"],
    }
    resp = client.post(f"{GRAPH_BASE}/schema/vertexlabels", json=vl)
    assert resp.status_code in (200, 201, 400), f"VertexLabel error: {resp.text}"

    # Create EdgeLabel
    el = {
        "name": labels["el"],
        "source_label": labels["vl"],
        "target_label": labels["vl"],
    }
    resp = client.post(f"{GRAPH_BASE}/schema/edgelabels", json=el)
    assert resp.status_code in (200, 201, 400), f"EdgeLabel error: {resp.text}"

    yield labels

    # Cleanup: delete all spike_test_ vertices, then remove labels
    _cleanup_spike_data(client)


def _cleanup_spike_data(client: httpx.Client) -> None:
    """Remove all spike_test_ prefixed data and schema from the graph."""
    # Delete vertices with spike_test_ label via Gremlin
    with contextlib.suppress(Exception):
        client.post(
            "/gremlin",
            json={"gremlin": f"{HUGEGRAPH_GRAPH}.traversal().V().hasLabel('{_TEST_PREFIX}person').drop().iterate()"},
        )
    time.sleep(0.5)

    # Remove EdgeLabel
    try:
        client.delete(f"{GRAPH_BASE}/schema/edgelabels/{_TEST_PREFIX}knows")
        time.sleep(0.3)
    except httpx.HTTPStatusError:
        pass

    # Remove VertexLabel
    try:
        client.delete(f"{GRAPH_BASE}/schema/vertexlabels/{_TEST_PREFIX}person")
        time.sleep(0.3)
    except httpx.HTTPStatusError:
        pass

    # Remove PropertyKeys
    for pk_name in [f"{_TEST_PREFIX}name", f"{_TEST_PREFIX}age"]:
        with contextlib.suppress(httpx.HTTPStatusError):
            client.delete(f"{GRAPH_BASE}/schema/propertykeys/{pk_name}")


# ---------------------------------------------------------------------------
# Connectivity tests (read-only, safe on any graph)
# ---------------------------------------------------------------------------


@pytest.mark.spike
class TestHugeGraphConnectivity:
    """Verify HugeGraph Docker container is reachable and responsive."""

    def test_versions_endpoint(self, client: httpx.Client) -> None:
        """GET /versions should return HugeGraph version info."""
        resp = client.get("/versions")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "version" in data or "versions" in data

    def test_graph_schema_accessible(self, client: httpx.Client) -> None:
        """GET /graphs/{name}/schema should return (possibly empty) schema."""
        resp = client.get(f"{GRAPH_BASE}/schema")
        # 200 if graph exists, 404 if not yet created — both mean server is reachable
        assert resp.status_code in (200, 404), f"Unexpected status: {resp.status_code}"

    def test_graph_mode(self, client: httpx.Client) -> None:
        """GET /graphs/{name}/mode should return the graph mode."""
        resp = client.get(f"{GRAPH_BASE}/mode")
        assert resp.status_code in (200, 404), f"Unexpected status: {resp.status_code}"

    def test_existing_vertex_labels(self, client: httpx.Client) -> None:
        """Verify the existing graph has monitoring-related vertex labels."""
        resp = client.get(f"{GRAPH_BASE}/schema/vertexlabels")
        assert resp.status_code == 200
        data = resp.json()
        names = [vl["name"] for vl in data.get("vertexlabels", [])]
        assert "monitor_point" in names, f"Expected monitor_point in {names}"

    def test_gremlin_query_existing_data(self, client: httpx.Client) -> None:
        """Query existing vertices via Gremlin (read-only)."""
        result = _gremlin(
            client,
            f"{HUGEGRAPH_GRAPH}.traversal().V().hasLabel('monitor_point').limit(3).count()",
        )
        count = result["result"]["data"]
        assert isinstance(count, list) and len(count) == 1
        assert count[0] >= 0


# ---------------------------------------------------------------------------
# CRUD tests (use spike_test_ prefixed labels, cleaned up after module)
# ---------------------------------------------------------------------------


@pytest.mark.spike
class TestHugeGraphBasicCRUD:
    """Verify basic Schema + Vertex + Edge operations using isolated test labels.

    All test data uses spike_test_ prefixed labels to avoid interfering with
    existing monitoring data on the shared 'hugegraph' graph.
    """

    def test_schema_exists(self, client: httpx.Client, test_labels: dict) -> None:
        """Verify spike_test_ schema was created by the fixture."""
        vl_name = test_labels["vl"]
        resp = client.get(f"{GRAPH_BASE}/schema/vertexlabels")
        assert resp.status_code == 200
        names = [vl["name"] for vl in resp.json().get("vertexlabels", [])]
        assert vl_name in names

    def test_vertex_crud(self, client: httpx.Client, test_labels: dict) -> None:
        """Create, read, and delete a vertex with spike_test_ label."""
        vl = test_labels["vl"]
        name_key = f"{_TEST_PREFIX}name"
        age_key = f"{_TEST_PREFIX}age"

        # Create vertex
        vertex = {"label": vl, "properties": {name_key: "marko", age_key: 29}}
        resp = client.post(f"{GRAPH_BASE}/graph/vertices", json=vertex)
        assert resp.status_code == 201, f"Vertex create failed: {resp.text}"
        vertex_id = resp.json()["id"]
        assert vertex_id  # e.g. "spike_test_person:marko"

        # Read vertex
        resp = client.get(f'{GRAPH_BASE}/graph/vertices/"{vertex_id}"')
        assert resp.status_code == 200
        data = resp.json()
        assert data["properties"][name_key] == "marko"

        # Delete vertex (immediate cleanup within test)
        resp = client.delete(f'{GRAPH_BASE}/graph/vertices/"{vertex_id}"')
        assert resp.status_code == 204

    def test_edge_crud(self, client: httpx.Client, test_labels: dict) -> None:
        """Create and read an edge between two spike_test_ vertices."""
        vl = test_labels["vl"]
        el = test_labels["el"]
        name_key = f"{_TEST_PREFIX}name"

        # Create two vertices and capture their actual IDs
        vertex_ids = {}
        for name in ["marko", "josh"]:
            resp = client.post(
                f"{GRAPH_BASE}/graph/vertices",
                json={"label": vl, "properties": {name_key: name}},
            )
            assert resp.status_code == 201, f"Vertex create failed: {resp.text}"
            vertex_ids[name] = resp.json()["id"]

        # Create edge using actual vertex IDs
        # HugeGraph 1.7.0 uses outV/outVLabel/inV/inVLabel
        edge = {
            "label": el,
            "outV": vertex_ids["marko"],
            "outVLabel": vl,
            "inV": vertex_ids["josh"],
            "inVLabel": vl,
            "properties": {},
        }
        resp = client.post(f"{GRAPH_BASE}/graph/edges", json=edge)
        assert resp.status_code == 201, f"Edge create failed: {resp.text}"

        # Query edge via Gremlin using actual vertex ID
        result = _gremlin(
            client,
            f"{HUGEGRAPH_GRAPH}.traversal().V('{vertex_ids['marko']}').out('{el}').values('{name_key}')",
        )
        data = result["result"]["data"]
        assert "josh" in str(data)

    def test_gremlin_query(self, client: httpx.Client, test_labels: dict) -> None:
        """Execute a Gremlin query on spike_test_ vertices."""
        vl = test_labels["vl"]
        name_key = f"{_TEST_PREFIX}name"
        age_key = f"{_TEST_PREFIX}age"

        # Create vertex
        client.post(
            f"{GRAPH_BASE}/graph/vertices",
            json={"label": vl, "properties": {name_key: "gremlin_test", age_key: 42}},
        )

        # Gremlin query
        result = _gremlin(
            client,
            f"{HUGEGRAPH_GRAPH}.traversal().V().hasLabel('{vl}').has('{name_key}','gremlin_test').values('{age_key}')",
        )
        assert result["result"]["data"] == [42]

    def test_batch_vertex_insert(self, client: httpx.Client, test_labels: dict) -> None:
        """Batch insert vertices via POST /vertices/batch."""
        vl = test_labels["vl"]
        name_key = f"{_TEST_PREFIX}name"
        age_key = f"{_TEST_PREFIX}age"

        vertices = [
            {"label": vl, "properties": {name_key: f"user{i}", age_key: 20 + i}}
            for i in range(10)
        ]
        resp = client.post(f"{GRAPH_BASE}/graph/vertices/batch", json=vertices)
        assert resp.status_code == 201, f"Batch insert failed: {resp.text}"
        ids = resp.json()
        assert len(ids) == 10
