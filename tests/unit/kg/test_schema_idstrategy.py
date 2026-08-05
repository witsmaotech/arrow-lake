"""Tests for v1.10.2 P0.3 edge sort_keys schema field.

实测修正(2026-08-05,HugeGraph 1.3.0 live test):edge label **没有 id_strategy
字段**(vertex-only);edge 幂等靠 sort_keys 单独决定。原 review C1 "需 id_strategy
=PRIMARY_KEY" 被推翻。这些测试钉死该结论,防止回归。
"""

from __future__ import annotations

from arrow_lake.knowledge_graph.schema import (
    ARROW_LAKE_KG_SCHEMA,
    EdgeLabelDef,
    GraphSchema,
    PropertyKeyDef,
    VertexLabelDef,
    schema_to_hugegraph_payload,
)


def test_edge_label_has_no_id_strategy_field() -> None:
    """1.3.0 edge labels have no id_strategy — field must not exist."""
    assert "id_strategy" not in EdgeLabelDef.__dataclass_fields__
    e = EdgeLabelDef("contains_chunk", "document", "chunk")
    assert e.sort_keys == ()


def test_edge_label_with_sort_keys() -> None:
    e = EdgeLabelDef(
        "references", "chunk", "entity", ("source_chunk_id",),
        sort_keys=("source_chunk_id",),
    )
    assert e.sort_keys == ("source_chunk_id",)


def test_payload_emits_sort_keys_not_id_strategy() -> None:
    """Payload must carry sort_keys but NEVER id_strategy (1.3.0 rejects it)."""
    schema = GraphSchema(
        property_keys=(
            PropertyKeyDef("name", "TEXT", "SINGLE"),
            PropertyKeyDef("src", "TEXT", "SINGLE"),
        ),
        vertex_labels=(VertexLabelDef("entity", ("name",), ("name",)),),
        edge_labels=(
            EdgeLabelDef("references", "chunk", "entity", ("src",), sort_keys=("src",)),
        ),
        index_labels=(),
    )
    el = schema_to_hugegraph_payload(schema)["edge_labels"][0]
    assert el["sort_keys"] == ["src"]
    assert "id_strategy" not in el  # critical — posting it ⇒ 400 unrecognized


def test_existing_schema_edges_default_empty_sort_keys() -> None:
    """P0.3 must NOT change existing edges — sort_keys stay empty until M2."""
    for el in ARROW_LAKE_KG_SCHEMA.edge_labels:
        assert el.sort_keys == ()


def test_existing_schema_payload_carries_no_id_strategy() -> None:
    """Regression guard: no edge payload field triggers 1.3.0 'unrecognized'."""
    payload = schema_to_hugegraph_payload(ARROW_LAKE_KG_SCHEMA)
    assert len(payload["edge_labels"]) == 14
    for el in payload["edge_labels"]:
        assert "id_strategy" not in el
        assert el["sort_keys"] == []


def test_vertex_labels_keep_id_strategy() -> None:
    """Vertex id_strategy (PRIMARY_KEY) is unaffected — regression guard."""
    for vl in schema_to_hugegraph_payload(ARROW_LAKE_KG_SCHEMA)["vertex_labels"]:
        assert vl["id_strategy"] == "PRIMARY_KEY"
