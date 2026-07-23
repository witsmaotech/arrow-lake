"""Unit tests for KG relation-type snapping (v1.9.2 批3).

After the entity-type enum was snapped, relation_type stayed noisy (186 distinct
values — English variants / synonyms). _ka_to_extraction_result now accepts a
``valid_relations`` enum and snaps non-enum relation types into the template's
declared vocabulary. See memory issue_kg_mapping_layer_fix.
"""

from __future__ import annotations

from types import SimpleNamespace

from arrow_lake.knowledge_graph.he_extractor import HyperExtractExtractor


def _ka(nodes, edges):
    """Build a fake hyper-extract KA with nodes/edges attributes."""
    return SimpleNamespace(
        nodes=[SimpleNamespace(**n) for n in nodes],
        edges=[SimpleNamespace(**e) for e in edges],
    )


def test_non_enum_relation_snaps_to_generic():
    # Arrange: LLM emitted English "related_to" which is not in the enum.
    ka = _ka(
        nodes=[{"name": "甲系统", "type": "系统", "definition": "d"},
               {"name": "乙组件", "type": "组件", "definition": "d"}],
        edges=[{"source": "甲系统", "target": "乙组件", "type": "related_to", "description": ""}],
    )
    valid_relations = ["属于", "包含", "相关", "导致"]

    # Act
    result = HyperExtractExtractor._ka_to_extraction_result(
        ka, valid_relations=valid_relations)

    # Assert: snapped to "相关" (the generic member present in the enum).
    assert len(result.relations) == 1
    assert result.relations[0].relation_type == "相关"


def test_enum_relation_preserved():
    # Arrange: an already-valid enum relation must NOT be altered.
    ka = _ka(
        nodes=[{"name": "甲系统", "type": "系统", "definition": "d"},
               {"name": "乙组件", "type": "组件", "definition": "d"}],
        edges=[{"source": "甲系统", "target": "乙组件", "type": "包含", "description": ""}],
    )
    valid_relations = ["属于", "包含", "相关"]

    result = HyperExtractExtractor._ka_to_extraction_result(
        ka, valid_relations=valid_relations)

    assert result.relations[0].relation_type == "包含"


def test_no_enum_no_snap():
    # Arrange: no enum (None) → relation passes through unchanged (generic
    # concept_graph templates have no strict relation enum).
    ka = _ka(
        nodes=[{"name": "甲系统", "type": "概念", "definition": "d"},
               {"name": "乙组件", "type": "概念", "definition": "d"}],
        edges=[{"source": "甲系统", "target": "乙组件", "type": "custom_relation", "description": ""}],
    )

    result = HyperExtractExtractor._ka_to_extraction_result(ka, valid_relations=None)

    assert result.relations[0].relation_type == "custom_relation"


def test_snap_falls_back_to_first_when_generic_absent():
    # Arrange: enum lacks "相关"; non-enum value snaps to first member.
    ka = _ka(
        nodes=[{"name": "甲系统", "type": "系统", "definition": "d"},
               {"name": "乙组件", "type": "组件", "definition": "d"}],
        edges=[{"source": "甲系统", "target": "乙组件", "type": "is_a", "description": ""}],
    )
    valid_relations = ["属于", "导致"]

    result = HyperExtractExtractor._ka_to_extraction_result(
        ka, valid_relations=valid_relations)

    assert result.relations[0].relation_type == "属于"
