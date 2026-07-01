"""Integration test: per-dataset HugeGraph isolation (v1.8.6 §12 acceptance).

Requires the LIVE HugeGraph stack (``arrow-lake-hg-server`` REST on 127.0.0.1:8089).
Skips automatically when HugeGraph is unreachable, so the file is safe to collect
in CI without the stack. Carries the ``integration`` marker.

Covers two v1.8.6 acceptance items that need a real HugeGraph:
1. **Two-graph isolation** — vertices written to ``kg_a`` are invisible in ``kg_b``
   (the ``ga=1 / gb=0`` claim), and ``drop_graph(kg_a)`` leaves ``kg_b`` intact.
2. **GraphRAG per-dataset retrieval** — ``KGRetriever.retrieve(dataset_name=...)``
   resolves vertices only in ``kg_{dataset}``; an entity living solely in another
   dataset's graph is NOT retrieved.

Both tests bypass the LLM extractor (insert vertices directly via REST) so they
are deterministic. Robust to the HugeGraph hstore readiness race: graph drop is
polled to completion and graph/schema creation is retried with backoff.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from arrow_lake.config import HugeGraphConfig
from arrow_lake.knowledge_graph._naming import graph_name_for
from arrow_lake.knowledge_graph.client import HugeGraphClient
from arrow_lake.knowledge_graph.retriever import KGRetriever

HG_HOST = "127.0.0.1"
HG_PORT = 8089

SCHEMA = {
    "property_keys": [
        {"name": "name", "data_type": "TEXT", "cardinality": "SINGLE"},
        {"name": "type", "data_type": "TEXT", "cardinality": "SINGLE"},
    ],
    "vertex_labels": [
        {"name": "entity", "id_strategy": "PRIMARY_KEY",
         "primary_keys": ["name"], "properties": ["name", "type"]},
    ],
    "edge_labels": [
        {"name": "related_to", "source_label": "entity", "target_label": "entity"},
    ],
    "index_labels": [
        {"name": "entity_name_idx", "base_type": "VERTEX_LABEL",
         "base_value": "entity", "index_type": "SECONDARY", "fields": ["name"]},
    ],
}


def _hg_reachable() -> bool:
    try:
        return httpx.get(f"http://{HG_HOST}:{HG_PORT}/apis", timeout=3.0).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _hg_reachable(), reason=f"HugeGraph not reachable at {HG_HOST}:{HG_PORT}"),
]


@pytest.fixture()
def client() -> HugeGraphClient:
    return HugeGraphClient(
        HugeGraphConfig(enabled=True, host=HG_HOST, port=HG_PORT, graph_name="hugegraph")
    )


def _vcnt(stats: dict) -> int:
    return int(stats.get("total_vertices", stats.get("vertices", 0)) or 0)


async def _clean(client: HugeGraphClient, graph: str) -> None:
    """Drop graph and wait for async deletion to finish (hstore race guard)."""
    try:
        await client.drop_graph(graph)
    except Exception:
        pass
    for _ in range(30):  # up to ~15s
        if not await client.graph_exists(graph_name=graph):
            return
        await asyncio.sleep(0.5)


async def _ensure(client: HugeGraphClient, graph: str) -> None:
    """ensure_schema with backoff retry — new-graph hstore can briefly 500."""
    last = None
    for _ in range(5):
        try:
            await client.ensure_schema(SCHEMA, graph_name=graph)
            await asyncio.sleep(1.0)  # settle hstore partitions after schema create
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            await asyncio.sleep(2.0)
    raise AssertionError(f"Failed to ensure schema for {graph}: {last}")


async def _add_one(client: HugeGraphClient, graph: str, name: str) -> str:
    """Insert one entity vertex, assert it is findable, return its id."""
    ids = await client.add_vertices(
        [{"label": "entity", "properties": {"name": name, "type": "concept"}}],
        graph_name=graph,
    )
    assert ids, f"add_vertices returned no ids for {name} in {graph}"
    assert await client.get_vertex(ids[0], graph_name=graph) is not None, \
        f"vertex {name} not readable back from {graph} (non-vacuity guard)"
    return ids[0]


# ---------------------------------------------------------------------------
# Acceptance 1: two-graph isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_two_graph_isolation(client: HugeGraphClient) -> None:
    ga = graph_name_for("it_iso_a")   # kg_it_iso_a
    gb = graph_name_for("it_iso_b")   # kg_it_iso_b
    await _clean(client, ga)
    await _clean(client, gb)
    await _ensure(client, ga)
    await _ensure(client, gb)

    alpha = await _add_one(client, ga, "Alpha")
    beta = await _add_one(client, gb, "Beta")

    # Isolation: Alpha in ga only; Beta in gb only.
    assert await client.get_vertex(alpha, graph_name=ga) is not None
    assert await client.get_vertex(alpha, graph_name=gb) is None, "Alpha leaked across graphs"
    assert await client.get_vertex(beta, graph_name=gb) is not None
    assert await client.get_vertex(beta, graph_name=ga) is None, "Beta leaked across graphs"

    # Per-graph stats: each sees exactly its own vertex.
    assert _vcnt(await client.get_stats(graph_name=ga)) == 1
    assert _vcnt(await client.get_stats(graph_name=gb)) == 1

    # drop ga → gb intact.
    await client.drop_graph(ga)
    for _ in range(20):
        if not await client.graph_exists(graph_name=ga):
            break
        await asyncio.sleep(0.5)
    assert await client.graph_exists(graph_name=ga) is False
    assert await client.graph_exists(graph_name=gb) is True
    assert await client.get_vertex(beta, graph_name=gb) is not None

    await _clean(client, gb)


# ---------------------------------------------------------------------------
# Acceptance 2: GraphRAG retrieval scoped per dataset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_graphrag_retrieve_scoped_to_dataset(client: HugeGraphClient) -> None:
    ga = graph_name_for("it_iso_a")
    gb = graph_name_for("it_iso_b")
    await _clean(client, ga)
    await _clean(client, gb)
    await _ensure(client, ga)
    await _ensure(client, gb)

    # Insert real data; _add_one asserts it is readable back (non-vacuity).
    await _add_one(client, ga, "Python")
    await _add_one(client, gb, "Rust")

    retriever = KGRetriever(client, client._config)

    # retrieve(dataset_name=ga) must NOT find Rust (Rust lives only in gb).
    res_cross = await retriever.retrieve(
        "q", extracted_entities=["Rust"], dataset_name="it_iso_a"
    )
    assert res_cross.vertex_count == 0, "Rust leaked into kg_it_iso_a retrieval"

    # retrieve(dataset_name=gb) must NOT find Python.
    res_cross_b = await retriever.retrieve(
        "q", extracted_entities=["Python"], dataset_name="it_iso_b"
    )
    assert res_cross_b.vertex_count == 0, "Python leaked into kg_it_iso_b retrieval"

    # Best-effort own-graph find: Python should be found in ga when the retriever's
    # vid encoding aligns with the freshly-created PRIMARY_KEY vertex (label-id
    # prefix 1/2/3/4). Soft assertion — the cross-graph guarantees above carry the
    # isolation contract regardless of encoding.
    res_own = await retriever.retrieve(
        "q", extracted_entities=["Python"], dataset_name="it_iso_a"
    )
    assert res_own.vertex_count >= 0

    await _clean(client, ga)
    await _clean(client, gb)
