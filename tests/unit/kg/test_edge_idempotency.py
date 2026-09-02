"""v1.10.2 M2/P3 — HugeGraph edge idempotency + multiplicity (live integration).

Pins the empirically-verified edge model (HugeGraph core 1.7.0, 2026-08-05) so
it cannot silently regress:

* ``frequency=SINGLE`` (default): edge unique per ``(src, dst, label)``; re-insert
  **upserts** (no accumulation) — structural edges are idempotent with empty
  ``sort_keys``.
* ``frequency=MULTIPLE`` + ``sort_keys=[relation_type]``: distinct relations
  between the same pair survive as separate edges (fixes the prior SINGLE
  collapse-to-one data loss); re-extracting the same relation upserts.

Requires the local HugeGraph at ``127.0.0.1:8089`` (admin/pa). Skipped when it is
unreachable (CI / no-Docker environments). Marked ``@pytest.mark.integration``.

Note on the persistent probe graph: we NEVER drop/clear it inside the test run —
a graph DROP dirties HugeGraph's in-memory schema cache (needs a server restart
before the next ensure_schema, see CLAUDE.md). The graph is created idempotently
and reused; each test uses its own vertex pair so counts are isolated. Clean up
manually with ``make kg-drop-graph DS=m2test_idem`` (+ restart hg-server).
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from arrow_lake.config import HugeGraphConfig
from arrow_lake.knowledge_graph.client import HugeGraphClient
from arrow_lake.knowledge_graph.schema import (
    EdgeLabelDef,
    GraphSchema,
    PropertyKeyDef,
    VertexLabelDef,
    schema_to_hugegraph_payload,
)

_HG_HOST = os.environ.get("ARROW_LAKE__HUGEGRAPH__HOST", "127.0.0.1")
_HG_PORT = int(os.environ.get("ARROW_LAKE__HUGEGRAPH__PORT", "8089"))
_HG_USER = os.environ.get("ARROW_LAKE__HUGEGRAPH__USERNAME", "admin")
_HG_PASS = os.environ.get("ARROW_LAKE__HUGEGRAPH__PASSWORD", "pa")

# Minimal probe schema: one PRIMARY_KEY vertex label; one relation edge label
# (MULTIPLE + sort_keys, mirrors production relation edges); one structural
# edge label (SINGLE, empty sort_keys, mirrors contains_chunk/references).
_PROBE_SCHEMA = GraphSchema(
    property_keys=(
        PropertyKeyDef("name", "TEXT", "SINGLE"),
        PropertyKeyDef("relation_type", "TEXT", "SINGLE"),
    ),
    vertex_labels=(VertexLabelDef("probe_v", ("name",), ("name",)),),
    edge_labels=(
        EdgeLabelDef(
            "e_rel", "probe_v", "probe_v", ("relation_type",),
            sort_keys=("relation_type",), frequency="MULTIPLE",
        ),
        EdgeLabelDef("e_struct", "probe_v", "probe_v"),
    ),
    index_labels=(),
)

_PROBE_GRAPH = "kg_m2test_idem"


def _hg_reachable() -> bool:
    """True if the local HugeGraph answers an AUTH-ENFORCED endpoint within 2s.

    /versions is unauthenticated — it 200'd with stale credentials while every
    real graphspace call 401'd (2026-09-02), so the probe must hit /graphs
    (auth-enforced) to prove BOTH reachability and valid credentials.
    """
    import httpx  # local import — only needed when the test runs

    try:
        r = httpx.get(
            f"http://{_HG_HOST}:{_HG_PORT}/graphs",
            auth=(_HG_USER, _HG_PASS), timeout=2.0,
        )
        return r.status_code == 200
    except Exception:  # noqa: BLE001 — any failure ⇒ skip
        return False


pytestmark = pytest.mark.skipif(
    not _hg_reachable(),
    reason=f"HugeGraph not reachable at {_HG_HOST}:{_HG_PORT} (set "
    "ARROW_LAKE__HUGEGRAPH__HOST/PORT or start the stack)",
)


def _client() -> HugeGraphClient:
    cfg = HugeGraphConfig(
        enabled=True, host=_HG_HOST, port=_HG_PORT, graph_name=_PROBE_GRAPH,
        username=_HG_USER, password=_HG_PASS, timeout_seconds=30.0,
    )
    return HugeGraphClient(cfg)


@pytest.fixture(scope="module")
async def probe_graph() -> str:
    """Ensure the probe graph + schema exist (idempotent). Never drops — see
    module docstring. Returns the graph name."""
    client = _client()
    await client.ensure_graph(graph_name=_PROBE_GRAPH)
    await client.ensure_schema(
        schema_to_hugegraph_payload(_PROBE_SCHEMA), graph_name=_PROBE_GRAPH,
    )
    await client.close()
    return _PROBE_GRAPH


async def _vertex(client: HugeGraphClient, graph_name: str, name: str) -> str:
    """Create (or upsert) one probe_v vertex; return its HugeGraph id."""
    ids = await client.add_vertices(
        [{"label": "probe_v", "properties": {"name": name}}],
        graph_name=graph_name,
    )
    assert ids and ids[0], f"no vertex id returned for {name}"
    return ids[0]


async def _count_edges(
    client: HugeGraphClient, graph_name: str, label: str, vertex_id: str,
) -> int:
    """Count edges of `label` incident to `vertex_id` (vertex-isolated count)."""
    edges = await client.get_vertex_edges(
        vertex_id, graph_name=graph_name, direction="OUT", limit=500,
    )
    return sum(1 for e in edges if e.get("label") == label)


def _edge(
    label: str, outv: str, inv: str, relation_type: str | None = None,
) -> dict[str, Any]:
    props: dict[str, Any] = {}
    if relation_type is not None:
        props["relation_type"] = relation_type
    return {
        "label": label,
        "outV": outv, "outVLabel": "probe_v",
        "inV": inv, "inVLabel": "probe_v",
        "properties": props,
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_relation_edge_same_type_is_idempotent(probe_graph: str) -> None:
    """Re-posting the same relation (same endpoints + relation_type) upserts →
    edge count stays 1 across two posts (G9 idempotency)."""
    client = _client()
    try:
        va = await _vertex(client, probe_graph, "M2_idem_A")
        vb = await _vertex(client, probe_graph, "M2_idem_B")
        edge = _edge("e_rel", va, vb, "uses")

        await client.add_edges([edge], graph_name=probe_graph)
        await client.add_edges([edge], graph_name=probe_graph)  # re-insert

        assert await _count_edges(client, probe_graph, "e_rel", va) == 1
    finally:
        await client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_relation_edge_distinct_types_preserved(probe_graph: str) -> None:
    """Two different relation_types between the same pair → 2 distinct edges
    (the M2 fix: under SINGLE they would collapse to one = data loss)."""
    client = _client()
    try:
        va = await _vertex(client, probe_graph, "M2_multi_A")
        vb = await _vertex(client, probe_graph, "M2_multi_B")

        await client.add_edges([_edge("e_rel", va, vb, "uses")], graph_name=probe_graph)
        await client.add_edges([_edge("e_rel", va, vb, "deploys")], graph_name=probe_graph)

        assert await _count_edges(client, probe_graph, "e_rel", va) == 2
    finally:
        await client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_structural_edge_is_idempotent(probe_graph: str) -> None:
    """SINGLE-frequency structural edge: re-post A→B twice → count stays 1
    (structural edges are already idempotent without sort_keys)."""
    client = _client()
    try:
        va = await _vertex(client, probe_graph, "M2_struct_A")
        vb = await _vertex(client, probe_graph, "M2_struct_B")
        edge = _edge("e_struct", va, vb)

        await client.add_edges([edge], graph_name=probe_graph)
        await client.add_edges([edge], graph_name=probe_graph)  # re-insert

        assert await _count_edges(client, probe_graph, "e_struct", va) == 1
    finally:
        await client.close()
