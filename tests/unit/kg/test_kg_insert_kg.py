"""Unit tests for ``KGBuilder._insert_kg`` (shared entity/relation insertion).

Focuses on the v1.8.8 dataset-mode ``references`` expansion (``entity_chunks``),
which is the new logic not exercised by the per-chunk ``test_kg_builder.py``.
Uses a fake HugeGraph client — no live graph needed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from arrow_lake.knowledge_graph.builder import KGBuilder
from arrow_lake.knowledge_graph.extractor import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)


class _MockClient:
    """Records add_vertices / add_edges calls; returns synthetic ids."""

    def __init__(self) -> None:
        self.added_vertices: list[dict] = []
        self.added_edges: list[dict] = []
        self._v = 0

    async def add_vertices(self, verts, graph_name=None):
        ids = []
        for _ in verts:
            ids.append(f"v{self._v}")
            self._v += 1
        self.added_vertices.extend(verts)
        return ids

    async def add_edges(self, edges, graph_name=None):
        self.added_edges.extend(edges)


def _make_builder(client: _MockClient) -> KGBuilder:
    b = KGBuilder.__new__(KGBuilder)
    b._client = client
    b._config = SimpleNamespace(write_concurrency=2)
    return b


class TestInsertKgDatasetMode:
    def test_references_expand_one_edge_per_owning_chunk(self):
        # Arrange — entity A owned by c0 and c2 (not c1)
        client = _MockClient()
        builder = _make_builder(client)
        result = ExtractionResult(
            entities=(ExtractedEntity(name="A", entity_type="concept"),),
            relations=(),
            raw_text="",
        )
        chunk_id_map = {"c0": "hg0", "c1": "hg1", "c2": "hg2"}
        entity_chunks = {"A": ["c0", "c2"]}

        # Act
        asyncio.run(builder._insert_kg(
            result, "kg_ds", chunk_id_map, entity_chunks=entity_chunks,
        ))

        # Assert — exactly 2 references edges: chunk(hg0)→A, chunk(hg2)→A
        ref_edges = [e for e in client.added_edges if e["label"] == "references"]
        assert len(ref_edges) == 2
        assert {e["outV"] for e in ref_edges} == {"hg0", "hg2"}
        assert all(e["inVLabel"] == "entity" for e in ref_edges)

    def test_relations_routed_and_related_to_fallback(self):
        # Arrange — two entities, one relation between them
        client = _MockClient()
        builder = _make_builder(client)
        result = ExtractionResult(
            entities=(
                ExtractedEntity(name="A", entity_type="concept"),
                ExtractedEntity(name="B", entity_type="concept"),
            ),
            relations=(
                ExtractedRelation(source="A", target="B", relation_type="uses"),
            ),
            raw_text="",
        )

        asyncio.run(builder._insert_kg(
            result, "kg_ds", {"c0": "hg0"}, owning_chunk_id="c0",
        ))

        # at least one relation edge (related_to or a routed typed label)
        rel_edges = [
            e for e in client.added_edges
            if e["label"] != "references"
        ]
        assert len(rel_edges) == 1
        assert rel_edges[0]["properties"]["relation_type"] == "uses"
