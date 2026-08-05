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
    # build_batch_size is required by _batch_add_vertices/_batch_add_edges
    # (was missing — pre-existing stale fixture, fixed in v1.9.10).
    b._config = SimpleNamespace(write_concurrency=2, build_batch_size=500)
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


class TestInsertKgRelationTypeSortKey:
    """v1.10.2 M2: relation_type is now a sort_key (relation edges are
    frequency=MULTIPLE + sort_keys=[relation_type]). HugeGraph rejects a null
    sort_key, so an empty/None relation_type must be coerced to a non-empty
    value (the routed edge label) — never null/empty on the posted edge."""

    def test_empty_relation_type_coerced_to_label(self):
        client = _MockClient()
        builder = _make_builder(client)
        result = ExtractionResult(
            entities=(
                ExtractedEntity(name="A", entity_type="concept"),
                ExtractedEntity(name="B", entity_type="concept"),
            ),
            relations=(
                ExtractedRelation(source="A", target="B", relation_type=""),
            ),
            raw_text="",
        )

        asyncio.run(builder._insert_kg(
            result, "kg_ds", {"c0": "hg0"}, owning_chunk_id="c0",
        ))

        rel_edges = [e for e in client.added_edges if e["label"] != "references"]
        assert len(rel_edges) == 1
        rt = rel_edges[0]["properties"]["relation_type"]
        # never null/empty (sort_key must be non-null); coerced to the label
        assert rt, f"relation_type must be non-empty, got {rt!r}"
        assert rt == rel_edges[0]["label"]


class TestInsertKgSourceChunk:
    """v1.9.10: entity vertex writes `source_chunk` (provenance chunk ids, SET)."""

    def test_per_chunk_writes_source_chunk(self):
        # Arrange — per-chunk path (owning_chunk_id) → source_chunk written
        client = _MockClient()
        builder = _make_builder(client)
        result = ExtractionResult(
            entities=(ExtractedEntity(
                name="响应时间", entity_type="指标",
                properties=(("definition", "系统响应时间"),),
            ),),
            relations=(),
            raw_text="",
        )

        # Act
        asyncio.run(builder._insert_kg(
            result, "kg_ds", {"c0": "hg0"}, owning_chunk_id="c0",
        ))

        # Assert — generic entity vertex carries source_chunk provenance
        ent = [v for v in client.added_vertices if v["label"] == "entity"]
        assert len(ent) == 1
        assert ent[0]["properties"]["source_chunk"] == ["c0"]

    def test_per_dataset_source_chunk_multi_and_value_default(self):
        # Arrange — per-dataset path: entity A owned by c0 + c5 → SET multi;
        # no value in properties → empty-string default.
        client = _MockClient()
        builder = _make_builder(client)
        result = ExtractionResult(
            entities=(ExtractedEntity(name="A", entity_type="concept"),),
            relations=(),
            raw_text="",
        )

        asyncio.run(builder._insert_kg(
            result, "kg_ds", {"c0": "hg0", "c5": "hg5"},
            entity_chunks={"A": ["c0", "c5"]},
        ))

        ent = [v for v in client.added_vertices if v["label"] == "entity"]
        assert ent[0]["properties"]["source_chunk"] == ["c0", "c5"]

    def test_source_chunk_omitted_when_no_provenance(self):
        # Arrange — entity absent from entity_chunks → source_chunk key must be
        # absent (not []): HugeGraph SET property does not accept empty list.
        client = _MockClient()
        builder = _make_builder(client)
        result = ExtractionResult(
            entities=(ExtractedEntity(name="孤儿实体", entity_type="concept"),),
            relations=(),
            raw_text="",
        )

        asyncio.run(builder._insert_kg(
            result, "kg_ds", {"c0": "hg0"},
            entity_chunks={"其他实体": ["c0"]},  # "孤儿实体" not present here
        ))

        ent = [v for v in client.added_vertices if v["label"] == "entity"]
        assert "source_chunk" not in ent[0]["properties"]
