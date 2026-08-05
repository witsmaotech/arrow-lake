"""Tests for v1.10.2 P0.3 + M2 edge schema: sort_keys + frequency.

实测修正(HugeGraph core 1.7.0,live test 2026-08-05):

1. Edge label **没有 ``id_strategy`` 字段**(vertex-only);edge 幂等不靠它。
2. Edge id = ``(src_vertex, label, sort_values, dst_vertex)`` —— 非随机,重插 **upsert**。
3. ``frequency=SINGLE``(默认):每 ``(src,dst,label)`` 唯一一条,重插 upsert → 结构边
   (``contains_chunk``/``references``/``next_chunk``)**本就幂等**,空 ``sort_keys`` 即可。
4. ``sort_keys`` **要求** ``frequency=MULTIPLE``(否则 400 *"can't contain sortKeys when
   the cardinality property is single"*)。
5. 关系边原默认 SINGLE → 同一顶点对不同关系类型**塌缩成一条边**(数据丢失)。M2 修:
   11 条关系边 ``frequency=MULTIPLE`` + ``sort_keys=[relation_type]`` → 不同 relation_type
   保留为独立边;重抽同关系 upsert → 跨重建幂等(G3/G9)。

这些测试钉死上述结论,防回归。
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

# 11 条关系边(MULTIPLE + sort_keys=[relation_type]);3 条结构边(SINGLE + 空 sort_keys)
RELATION_EDGES = frozenset({
    "related_to", "part_of", "deployed_on", "uses", "processes", "provides",
    "requires", "belongs_to", "located_in", "participates_in", "depicts",
})
STRUCTURAL_EDGES = frozenset({"contains_chunk", "references", "next_chunk"})


# -- M0 regression guards (unchanged conclusions) -----------------------------


def test_edge_label_has_no_id_strategy_field() -> None:
    """Edge labels have no id_strategy — field must not exist."""
    assert "id_strategy" not in EdgeLabelDef.__dataclass_fields__
    e = EdgeLabelDef("contains_chunk", "document", "chunk")
    assert e.sort_keys == ()
    assert e.frequency == "SINGLE"  # M2 default


def test_payload_emits_sort_keys_not_id_strategy() -> None:
    """Payload carries sort_keys but NEVER id_strategy (HugeGraph rejects it)."""
    schema = GraphSchema(
        property_keys=(
            PropertyKeyDef("name", "TEXT", "SINGLE"),
            PropertyKeyDef("rk", "TEXT", "SINGLE"),
        ),
        vertex_labels=(VertexLabelDef("entity", ("name",), ("name",)),),
        edge_labels=(
            EdgeLabelDef(
                "uses", "entity", "entity", ("rk",),
                sort_keys=("rk",), frequency="MULTIPLE",
            ),
        ),
        index_labels=(),
    )
    el = schema_to_hugegraph_payload(schema)["edge_labels"][0]
    assert el["sort_keys"] == ["rk"]
    assert el["frequency"] == "MULTIPLE"
    assert "id_strategy" not in el  # critical — posting it ⇒ 400 unrecognized


def test_vertex_labels_keep_id_strategy() -> None:
    """Vertex id_strategy (PRIMARY_KEY) is unaffected — regression guard."""
    for vl in schema_to_hugegraph_payload(ARROW_LAKE_KG_SCHEMA)["vertex_labels"]:
        assert vl["id_strategy"] == "PRIMARY_KEY"
        assert "frequency" not in vl  # frequency is edge-only


# -- M2: frequency + relation sort_keys ---------------------------------------


def test_structural_edges_single_frequency_empty_sort_keys() -> None:
    """Structural edges stay SINGLE + empty sort_keys — already idempotent
    (re-insert upserts by src/dst pair). Adding sort_keys would force MULTIPLE
    and wrongly ALLOW duplicate structural edges."""
    for el in ARROW_LAKE_KG_SCHEMA.edge_labels:
        if el.name in STRUCTURAL_EDGES:
            assert el.frequency == "SINGLE", el.name
            assert el.sort_keys == (), el.name


def test_relation_edges_multiple_frequency_with_sort_keys() -> None:
    """11 relation edges: frequency=MULTIPLE + sort_keys=[relation_type], so
    distinct relations between the same pair survive (fixes collapse-to-one)."""
    seen = set()
    for el in ARROW_LAKE_KG_SCHEMA.edge_labels:
        if el.name in RELATION_EDGES:
            assert el.frequency == "MULTIPLE", el.name
            assert el.sort_keys == ("relation_type",), el.name
            seen.add(el.name)
    assert seen == RELATION_EDGES


def test_sort_keys_subset_of_properties() -> None:
    """HugeGraph requires sort_keys ⊆ properties; and sort_keys ⇒ MULTIPLE."""
    for el in ARROW_LAKE_KG_SCHEMA.edge_labels:
        assert set(el.sort_keys) <= set(el.properties), f"{el.name}: sort_keys not subset"
        if el.sort_keys:
            assert el.frequency == "MULTIPLE", f"{el.name}: sort_keys require MULTIPLE"


def test_payload_emits_frequency_for_edges_only() -> None:
    """Payload emits frequency for every edge label (not vertices)."""
    payload = schema_to_hugegraph_payload(ARROW_LAKE_KG_SCHEMA)
    assert len(payload["edge_labels"]) == 14
    for el in payload["edge_labels"]:
        assert "id_strategy" not in el
        assert el["frequency"] in ("SINGLE", "MULTIPLE")
        if el["frequency"] == "MULTIPLE":
            assert el["sort_keys"] == ["relation_type"]
            assert el["name"] in RELATION_EDGES
        else:
            assert el["sort_keys"] == []
            assert el["name"] in STRUCTURAL_EDGES
    for vl in payload["vertex_labels"]:
        assert "frequency" not in vl
