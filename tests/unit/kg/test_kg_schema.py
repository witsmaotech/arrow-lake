"""Unit tests for KG schema definitions."""

from __future__ import annotations

from arrow_lake.knowledge_graph.schema import (
    ARROW_LAKE_KG_SCHEMA,
    EdgeLabelDef,
    GraphSchema,
    IndexLabelDef,
    PropertyKeyDef,
    VertexLabelDef,
    schema_to_hugegraph_payload,
)

# ---------------------------------------------------------------------------
# Dataclass construction
# ---------------------------------------------------------------------------


def test_property_key_def() -> None:
    pk = PropertyKeyDef(name="name", data_type="TEXT", cardinality="SINGLE")
    assert pk.name == "name"
    assert pk.data_type == "TEXT"


def test_vertex_label_def() -> None:
    vl = VertexLabelDef(
        name="entity",
        properties=("name", "type"),
        primary_keys=("name",),
        id_strategy="PRIMARY_KEY",
    )
    assert vl.name == "entity"
    assert vl.primary_keys == ("name",)


def test_edge_label_def() -> None:
    el = EdgeLabelDef(
        name="related_to",
        source_label="entity",
        target_label="entity",
        properties=("weight",),
    )
    assert el.source_label == "entity"


def test_index_label_def() -> None:
    il = IndexLabelDef(
        name="entity_name_idx",
        base_type="VERTEX_LABEL",
        base_value="entity",
        index_type="SECONDARY",
        fields=("name",),
    )
    assert il.index_type == "SECONDARY"


def test_frozen_immutability() -> None:
    pk = PropertyKeyDef(name="name", data_type="TEXT", cardinality="SINGLE")
    try:
        pk.name = "other"  # type: ignore[misc]
        raise AssertionError("Should have raised FrozenInstanceError")
    except AttributeError:
        pass


# ---------------------------------------------------------------------------
# GraphSchema
# ---------------------------------------------------------------------------


def test_graph_schema_construction() -> None:
    schema = GraphSchema(
        property_keys=(PropertyKeyDef("id", "TEXT", "SINGLE"),),
        vertex_labels=(VertexLabelDef("doc", ("id",), ("id",)),),
        edge_labels=(),
        index_labels=(),
    )
    assert len(schema.property_keys) == 1
    assert len(schema.vertex_labels) == 1


# ---------------------------------------------------------------------------
# ARROW_LAKE_KG_SCHEMA
# ---------------------------------------------------------------------------


def test_arrow_lake_kg_schema_structure() -> None:
    schema = ARROW_LAKE_KG_SCHEMA
    # 8 vertex labels: document, chunk, entity, person, organization, location, concept, event
    assert len(schema.vertex_labels) == 8
    vl_names = {vl.name for vl in schema.vertex_labels}
    assert vl_names == {
        "document", "chunk", "entity", "person",
        "organization", "location", "concept", "event",
    }

    # 14 edge labels (9 base + 5 project_concept_graph verb-driven domain edges)
    assert len(schema.edge_labels) == 14
    el_names = {el.name for el in schema.edge_labels}
    assert el_names == {
        "contains_chunk", "references", "next_chunk",
        "related_to", "part_of", "belongs_to",
        "located_in", "participates_in", "depicts",
        "deployed_on", "uses", "processes", "provides", "requires",
    }

    # Property keys must cover all vertex/edge properties
    assert len(schema.property_keys) > 0
    # index_labels intentionally empty (e3b4f09 removed the redundant
    # primary_key index); verify it stays empty rather than re-appearing.
    assert len(schema.index_labels) == 0


def test_domain_edge_labels_are_entity_to_entity() -> None:
    """Verb-driven domain edges are entity→entity with relation_type+description."""
    by_name = {el.name: el for el in ARROW_LAKE_KG_SCHEMA.edge_labels}
    for name in ("part_of", "deployed_on", "uses", "processes", "provides", "requires"):
        el = by_name[name]
        assert el.source_label == "entity"
        assert el.target_label == "entity"
        assert "relation_type" in el.properties
        assert "description" in el.properties


# ---------------------------------------------------------------------------
# schema_to_hugegraph_payload
# ---------------------------------------------------------------------------


def test_schema_to_payload_structure() -> None:
    schema = GraphSchema(
        property_keys=(
            PropertyKeyDef("name", "TEXT", "SINGLE"),
            PropertyKeyDef("weight", "DOUBLE", "SINGLE"),
        ),
        vertex_labels=(
            VertexLabelDef("entity", ("name",), ("name",)),
        ),
        edge_labels=(
            EdgeLabelDef("related_to", "entity", "entity", ("weight",)),
        ),
        index_labels=(
            IndexLabelDef("entity_name_idx", "VERTEX_LABEL", "entity", "SECONDARY", ("name",)),
        ),
    )
    payload = schema_to_hugegraph_payload(schema)

    assert "property_keys" in payload
    assert "vertex_labels" in payload
    assert "edge_labels" in payload
    assert "index_labels" in payload
    assert len(payload["property_keys"]) == 2
    assert len(payload["vertex_labels"]) == 1
    assert len(payload["edge_labels"]) == 1
    assert len(payload["index_labels"]) == 1


def test_schema_to_payload_field_types() -> None:
    payload = schema_to_hugegraph_payload(ARROW_LAKE_KG_SCHEMA)
    for pk in payload["property_keys"]:
        assert isinstance(pk, dict)
        assert "name" in pk
        assert "data_type" in pk
        assert "cardinality" in pk
    for vl in payload["vertex_labels"]:
        assert isinstance(vl, dict)
        assert "name" in vl
        assert "id_strategy" in vl
        assert "primary_keys" in vl
    for el in payload["edge_labels"]:
        assert isinstance(el, dict)
        assert "name" in el
        assert "source_label" in el
        assert "target_label" in el
