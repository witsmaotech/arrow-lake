"""Graph schema definitions for HugeGraph.

Frozen dataclasses define the Arrow Lake KG schema (8 vertex labels + 9 edge labels)
matching architecture document Section 5C.

Conversion function transforms schema defs to HugeGraph REST API payloads.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PropertyKeyDef:
    """A HugeGraph property key definition."""

    name: str
    data_type: str  # TEXT, INT, DOUBLE, DATE, FLOAT, LONG, BOOLEAN
    cardinality: str  # SINGLE, LIST, SET


@dataclass(frozen=True)
class VertexLabelDef:
    """A HugeGraph vertex label definition."""

    name: str
    properties: tuple[str, ...]
    primary_keys: tuple[str, ...]
    id_strategy: str = "PRIMARY_KEY"
    nullable_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class EdgeLabelDef:
    """A HugeGraph edge label definition."""

    name: str
    source_label: str
    target_label: str
    properties: tuple[str, ...] = ()


@dataclass(frozen=True)
class IndexLabelDef:
    """A HugeGraph index label definition."""

    name: str
    base_type: str  # VERTEX_LABEL, EDGE_LABEL
    base_value: str  # label name
    index_type: str  # SECONDARY, RANGE, UNIQUE
    fields: tuple[str, ...]


@dataclass(frozen=True)
class GraphSchema:
    """Complete graph schema: property keys, vertex labels, edge labels, index labels."""

    property_keys: tuple[PropertyKeyDef, ...]
    vertex_labels: tuple[VertexLabelDef, ...]
    edge_labels: tuple[EdgeLabelDef, ...]
    index_labels: tuple[IndexLabelDef, ...]


def schema_to_hugegraph_payload(schema: GraphSchema) -> dict:
    """Convert GraphSchema to HugeGraph REST API batch payload.

    Returns dict with keys: property_keys, vertex_labels, edge_labels, index_labels.
    Each value is a list of dicts matching HugeGraph schema API format.
    """
    return {
        "property_keys": [
            {"name": pk.name, "data_type": pk.data_type, "cardinality": pk.cardinality}
            for pk in schema.property_keys
        ],
        "vertex_labels": [
            {
                "name": vl.name,
                "id_strategy": vl.id_strategy,
                "primary_keys": list(vl.primary_keys),
                "properties": list(vl.properties),
                "nullable_keys": list(vl.nullable_keys),
            }
            for vl in schema.vertex_labels
        ],
        "edge_labels": [
            {
                "name": el.name,
                "source_label": el.source_label,
                "target_label": el.target_label,
                "properties": list(el.properties),
            }
            for el in schema.edge_labels
        ],
        "index_labels": [
            {
                "name": il.name,
                "base_type": il.base_type,
                "base_value": il.base_value,
                "index_type": il.index_type,
                "fields": list(il.fields),
            }
            for il in schema.index_labels
        ],
    }


# ---------------------------------------------------------------------------
# Arrow Lake KG Schema (8 vertex labels + 9 edge labels)
# Reference: Architecture doc Section 5C
# ---------------------------------------------------------------------------

ARROW_LAKE_KG_SCHEMA = GraphSchema(
    property_keys=(
        PropertyKeyDef("id", "TEXT", "SINGLE"),
        PropertyKeyDef("name", "TEXT", "SINGLE"),
        PropertyKeyDef("type", "TEXT", "SINGLE"),
        PropertyKeyDef("content", "TEXT", "SINGLE"),
        PropertyKeyDef("embedding_id", "TEXT", "SINGLE"),
        PropertyKeyDef("chunk_index", "INT", "SINGLE"),
        PropertyKeyDef("weight", "DOUBLE", "SINGLE"),
        PropertyKeyDef("doc_name", "TEXT", "SINGLE"),
        PropertyKeyDef("date", "TEXT", "SINGLE"),
        PropertyKeyDef("relation_type", "TEXT", "SINGLE"),
    ),
    vertex_labels=(
        VertexLabelDef("document", ("id", "name"), ("id",)),
        VertexLabelDef("chunk", ("id", "content", "chunk_index"), ("id",)),
        VertexLabelDef("entity", ("name", "type"), ("name",), nullable_keys=("type",)),
        VertexLabelDef("person", ("name",), ("name",)),
        VertexLabelDef("organization", ("name",), ("name",)),
        VertexLabelDef("location", ("name",), ("name",)),
        VertexLabelDef("concept", ("name",), ("name",)),
        VertexLabelDef("event", ("name", "date"), ("name",), nullable_keys=("date",)),
    ),
    edge_labels=(
        EdgeLabelDef("contains_chunk", "document", "chunk"),
        EdgeLabelDef("references", "chunk", "entity"),
        EdgeLabelDef("next_chunk", "chunk", "chunk"),
        EdgeLabelDef("related_to", "entity", "entity", ("weight", "relation_type")),
        EdgeLabelDef("part_of", "entity", "entity", ("relation_type",)),
        EdgeLabelDef("belongs_to", "person", "organization", ("relation_type",)),
        EdgeLabelDef("located_in", "person", "location", ("relation_type",)),
        EdgeLabelDef("participates_in", "person", "event", ("relation_type",)),
        EdgeLabelDef("depicts", "event", "entity", ("relation_type",)),
    ),
    index_labels=(
        IndexLabelDef("document_id_idx", "VERTEX_LABEL", "document", "SECONDARY", ("id",)),
        IndexLabelDef("chunk_id_idx", "VERTEX_LABEL", "chunk", "SECONDARY", ("id",)),
        IndexLabelDef("entity_name_idx", "VERTEX_LABEL", "entity", "SECONDARY", ("name",)),
        IndexLabelDef("person_name_idx", "VERTEX_LABEL", "person", "SECONDARY", ("name",)),
        IndexLabelDef("org_name_idx", "VERTEX_LABEL", "organization", "SECONDARY", ("name",)),
        IndexLabelDef("location_name_idx", "VERTEX_LABEL", "location", "SECONDARY", ("name",)),
        IndexLabelDef("concept_name_idx", "VERTEX_LABEL", "concept", "SECONDARY", ("name",)),
        IndexLabelDef("event_name_idx", "VERTEX_LABEL", "event", "SECONDARY", ("name",)),
    ),
)
