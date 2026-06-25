"""Verify v1.7.1 §4.5 A strategy end-to-end against a real HugeGraph.

Writes typed entities + a routable relation + a fallback relation through
``KGBuilder.execute_build``, then gremlin-verifies:

- double-write: entity (generic) + person/organization/concept (typed) vertices
- typed edge routing: ``works_at`` (person→organization) → ``belongs_to``
- fallback: ``knows`` (person→concept, no synonym) → ``related_to``
- ``relation_type`` preserved on both edge kinds

Uses a dedicated graph ``v17_he_verify`` (created/cleared each run) so the
production ``hugegraph`` graph is never touched. No LLM required (mock
extractor) — this isolates the builder/schema/router correctness, not the
LLM extraction quality (covered by test_extraction_accuracy_production).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pyarrow as pa

from arrow_lake.config import HugeGraphConfig
from arrow_lake.knowledge_graph.builder import KGBuilder
from arrow_lake.knowledge_graph.client import HugeGraphClient
from arrow_lake.knowledge_graph.extractor import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)

GRAPH = "v17_he_verify"


async def main() -> None:
    cfg = HugeGraphConfig(
        enabled=True, host="localhost", port=8089, graph_name=GRAPH,
        build_batch_size=10,
    )
    client = HugeGraphClient(cfg)

    existed = await client.graph_exists()
    if existed:
        await client.clear()
    else:
        await client.ensure_graph()
    print(f"[setup] graph {GRAPH!r} ready (pre-existed={existed})")

    extractor = AsyncMock()
    extractor.extract.return_value = ExtractionResult(
        entities=(
            ExtractedEntity(name="Alice", entity_type="person"),
            ExtractedEntity(name="Acme", entity_type="organization"),
            ExtractedEntity(name="Scheme", entity_type="concept"),
        ),
        relations=(
            ExtractedRelation(source="Alice", target="Acme", relation_type="works_at"),
            ExtractedRelation(source="Alice", target="Scheme", relation_type="knows"),
        ),
        raw_text="Alice works at Acme and knows Scheme.",
    )

    builder = KGBuilder(client, extractor, cfg)
    table = pa.table({
        "id": ["c1"],
        "content": ["Alice works at Acme and knows Scheme."],
        "document_name": ["doc.txt"],
        "chunk_index": [0],
    })
    task_id = await builder.build("verify_ds", table)
    await builder.execute_build(task_id)

    task = builder.get_task_status(task_id)
    print(
        f"[build] status={task.status.value} "
        f"entities={task.entity_count} relations={task.relation_count} "
        f"error={task.error}"
    )
    assert task.status.value == "COMPLETED", f"build failed: {task.error}"

    # Verify via direct graph API (NOT client.gremlin — it targets the default
    # `g` bound to the `hugegraph` graph, not this script's dedicated graph).
    import gzip
    import json
    import urllib.request

    base = f"http://{cfg.host}:{cfg.port}/graphs/{GRAPH}/graph"

    def _get(path: str) -> dict:
        req = urllib.request.Request(f"{base}/{path}", headers={"Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw)

    def _count(kind: str, label: str) -> int:
        return len(_get(f"{kind}?label={label}&limit=200").get(kind, []))

    person_n = _count("vertices", "person")
    org_n = _count("vertices", "organization")
    concept_n = _count("vertices", "concept")
    entity_n = _count("vertices", "entity")
    belongs_n = _count("edges", "belongs_to")
    related_n = _count("edges", "related_to")
    print(f"[verify] person={person_n} org={org_n} concept={concept_n} entity={entity_n} "
          f"belongs_to={belongs_n} related_to={related_n}")

    belongs_props = [e.get("properties", {}) for e in _get("edges?label=belongs_to&limit=1").get("edges", [])]
    related_props = [e.get("properties", {}) for e in _get("edges?label=related_to&limit=1").get("edges", [])]

    assert person_n >= 1, "person typed vertex missing (double-write)"
    assert org_n >= 1, "organization typed vertex missing"
    assert concept_n >= 1, "concept typed vertex missing"
    assert entity_n >= 3, "generic entity vertices missing"
    assert belongs_n == 1, "belongs_to not routed for works_at"
    assert related_n == 1, "related_to fallback missing for knows"
    assert "works_at" in str(belongs_props), "relation_type not on belongs_to"
    assert "knows" in str(related_props), "relation_type not on related_to"

    await client.clear()
    print("[cleanup] graph cleared")
    print("\n✅ A strategy verified end-to-end on real HugeGraph")


if __name__ == "__main__":
    asyncio.run(main())
