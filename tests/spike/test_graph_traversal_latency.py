"""Spike: Graph traversal performance benchmark.

Requires HugeGraph running (set ARROW_LAKE__HUGEGRAPH__HOST/PORT or
HUGEGRAPH_HOST/HUGEGRAPH_PORT).  Measures P50/P95/P99 latency for
2-hop traversals. Target: P95 < 1s.

Usage:
    uv run pytest tests/spike/test_graph_traversal_latency.py -v -m spike -s
"""



from __future__ import annotations

import pytest

pytestmark = pytest.mark.spike

import contextlib
import random
import time

import httpx
import pytest

from tests.conftest_services import (
    HUGEGRAPH_GRAPH,
    HUGEGRAPH_GRAPH_BASE,
    gremlin,
    make_hg_client,
    require_hugegraph,
)

NUM_VERTICES = 1000
NUM_EDGES = 2000
MAX_DEPTH = 2
TARGET_P95_MS = 1000.0

GRAPH_BASE = HUGEGRAPH_GRAPH_BASE


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    return make_hg_client(timeout=120.0)


@pytest.fixture(scope="module")
def benchmark_graph(client: httpx.Client):
    """Create a benchmark graph with NUM_VERTICES vertices and NUM_EDGES edges.

    Uses spike_bench_ prefixed labels. Cleans up after all tests.
    """
    vl_name = "spike_bench_node"
    el_name = "spike_bench_edge"
    pk_name = "spike_bench_id"
    pk_val = "spike_bench_value"

    for pk in [
        {"name": pk_name, "data_type": "INT", "cardinality": "SINGLE"},
        {"name": pk_val, "data_type": "DOUBLE", "cardinality": "SINGLE"},
    ]:
        resp = client.post(f"{GRAPH_BASE}/schema/propertykeys", json=pk)
        assert resp.status_code in (200, 201, 202, 400), f"PK error: {resp.text}"

    vl = {
        "name": vl_name,
        "id_strategy": "PRIMARY_KEY",
        "primary_keys": [pk_name],
        "properties": [pk_name, pk_val],
    }
    resp = client.post(f"{GRAPH_BASE}/schema/vertexlabels", json=vl)
    assert resp.status_code in (200, 201, 202, 400), f"VL error: {resp.text}"

    el = {
        "name": el_name,
        "source_label": vl_name,
        "target_label": vl_name,
        "properties": [pk_val],
        "nullable_keys": [pk_val],
    }
    resp = client.post(f"{GRAPH_BASE}/schema/edgelabels", json=el)
    assert resp.status_code in (200, 201, 202, 400), f"EL error: {resp.text}"

    time.sleep(1)

    vertices = [
        {"label": vl_name, "properties": {pk_name: i, pk_val: float(i)}}
        for i in range(NUM_VERTICES)
    ]
    resp = client.post(f"{GRAPH_BASE}/graph/vertices/batch", json=vertices)
    assert resp.status_code == 201, f"Batch vertex insert failed: {resp.text}"
    vertex_ids = resp.json()
    assert len(vertex_ids) == NUM_VERTICES

    random.seed(42)
    edges = []
    created: set[tuple[int, int]] = set()
    while len(edges) < NUM_EDGES:
        src_idx = random.randint(0, NUM_VERTICES - 1)
        tgt_idx = random.randint(0, NUM_VERTICES - 1)
        if src_idx == tgt_idx:
            continue
        edge_key = (min(src_idx, tgt_idx), max(src_idx, tgt_idx))
        if edge_key in created:
            continue
        created.add(edge_key)
        edges.append({
            "label": el_name,
            "outV": vertex_ids[src_idx],
            "outVLabel": vl_name,
            "inV": vertex_ids[tgt_idx],
            "inVLabel": vl_name,
            "properties": {pk_val: random.random()},
        })
    resp = client.post(f"{GRAPH_BASE}/graph/edges/batch", json=edges)
    assert resp.status_code == 201, f"Batch edge insert failed: {resp.text}"

    time.sleep(1)

    yield {"vl_name": vl_name, "el_name": el_name, "pk_name": pk_name, "vertex_ids": vertex_ids}

    # Cleanup
    with contextlib.suppress(Exception, pytest.skip.Exception):
        gremlin(client, f"{HUGEGRAPH_GRAPH}.traversal().V().hasLabel('{vl_name}').drop().iterate()")
    time.sleep(1)
    with contextlib.suppress(httpx.HTTPStatusError):
        client.delete(f"{GRAPH_BASE}/schema/edgelabels/{el_name}")
    time.sleep(0.5)
    with contextlib.suppress(httpx.HTTPStatusError):
        client.delete(f"{GRAPH_BASE}/schema/vertexlabels/{vl_name}")
    time.sleep(0.5)
    for name in [pk_name, pk_val]:
        with contextlib.suppress(httpx.HTTPStatusError):
            client.delete(f"{GRAPH_BASE}/schema/propertykeys/{name}")


def _percentile(data: list[float], p: float) -> float:
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    idx = min(idx, len(sorted_data) - 1)
    return sorted_data[idx]


@require_hugegraph
@pytest.mark.spike
class TestGraphTraversalLatency:
    """Benchmark 2-hop traversal latency on a random graph."""

    def test_graph_loaded(self, client: httpx.Client, benchmark_graph: dict) -> None:
        vl = benchmark_graph["vl_name"]
        v_count = gremlin(
            client,
            f"{HUGEGRAPH_GRAPH}.traversal().V().hasLabel('{vl}').count()",
        )
        assert v_count["result"]["data"][0] == NUM_VERTICES

    def test_kneighbor_latency(self, client: httpx.Client, benchmark_graph: dict) -> None:
        random.seed(123)
        sources = random.sample(range(NUM_VERTICES), min(10, NUM_VERTICES))

        latencies: list[float] = []
        for src_idx in sources:
            source_id = benchmark_graph["vertex_ids"][src_idx]
            start = time.perf_counter()
            resp = client.post(
                f"{GRAPH_BASE}/traversers/kneighbor",
                json={
                    "source": source_id,
                    "steps": {"direction": "OUT", "max_degree": 10000},
                    "max_depth": MAX_DEPTH,
                    "with_vertex": False,
                    "limit": 1000,
                },
            )
            elapsed = time.perf_counter() - start
            assert resp.status_code == 200, f"Kneighbor failed: {resp.text}"
            latencies.append(elapsed * 1000)

        p50 = _percentile(latencies, 50)
        p95 = _percentile(latencies, 95)
        p99 = _percentile(latencies, 99)

        result = {
            "operation": "kneighbor",
            "iterations": len(latencies),
            "p50_ms": p50,
            "p95_ms": p95,
            "p99_ms": p99,
            "target_p95_ms": TARGET_P95_MS,
            "passed": p95 < TARGET_P95_MS,
        }

        print(f"\n{'='*60}")
        print(f"Traversal Benchmark: K-Neighbor ({result['iterations']} iterations)")
        print(f"  Graph: {NUM_VERTICES} vertices, {NUM_EDGES} edges, depth={MAX_DEPTH}")
        print(f"{'='*60}")
        print(f"  P50: {result['p50_ms']:.1f} ms")
        print(f"  P95: {result['p95_ms']:.1f} ms (target: {result['target_p95_ms']:.0f} ms)")
        print(f"  P99: {result['p99_ms']:.1f} ms")
        print(f"  Result: {'PASS' if result['passed'] else 'FAIL'}")
        print(f"{'='*60}\n")

        assert result["passed"], f"P95={result['p95_ms']:.1f}ms exceeds target {TARGET_P95_MS}ms"

    def test_gremlin_2hop_latency(self, client: httpx.Client, benchmark_graph: dict) -> None:
        random.seed(456)
        sources = random.sample(range(NUM_VERTICES), min(10, NUM_VERTICES))

        latencies: list[float] = []
        for src_idx in sources:
            source_id = benchmark_graph["vertex_ids"][src_idx]
            start = time.perf_counter()
            gremlin(
                client,
                f"{HUGEGRAPH_GRAPH}.traversal().V('{source_id}')"
                f".repeat(__.out().simplePath()).times({MAX_DEPTH})"
                f".dedup().count()",
            )
            elapsed = time.perf_counter() - start
            latencies.append(elapsed * 1000)

        p50 = _percentile(latencies, 50)
        p95 = _percentile(latencies, 95)
        p99 = _percentile(latencies, 99)

        result = {
            "operation": "gremlin_2hop",
            "iterations": len(latencies),
            "p50_ms": p50,
            "p95_ms": p95,
            "p99_ms": p99,
            "target_p95_ms": TARGET_P95_MS,
            "passed": p95 < TARGET_P95_MS,
        }

        print(f"\n{'='*60}")
        print(f"Traversal Benchmark: Gremlin 2-hop ({result['iterations']} iterations)")
        print(f"  Graph: {NUM_VERTICES} vertices, {NUM_EDGES} edges, depth={MAX_DEPTH}")
        print(f"{'='*60}")
        print(f"  P50: {result['p50_ms']:.1f} ms")
        print(f"  P95: {result['p95_ms']:.1f} ms (target: {result['target_p95_ms']:.0f} ms)")
        print(f"  P99: {result['p99_ms']:.1f} ms")
        print(f"  Result: {'PASS' if result['passed'] else 'FAIL'}")
        print(f"{'='*60}\n")

        assert result["passed"], f"P95={result['p95_ms']:.1f}ms exceeds target {TARGET_P95_MS}ms"
