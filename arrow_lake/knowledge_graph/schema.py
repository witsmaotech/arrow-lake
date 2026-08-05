"""Graph schema definitions for HugeGraph.

Frozen dataclasses define the Arrow Lake KG schema (8 vertex labels + 14 edge labels)
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
    """A HugeGraph edge label definition.

    HugeGraph edge id (实测 core 1.7.0, 2026-08-05): unlike vertex labels, edge
    labels have **NO ``id_strategy`` field** — edge id is deterministically
    derived from ``(source_vertex_id, target_vertex_id, sort_key_values,
    label)``. So an edge is idempotent (re-insert upserts) **iff ``sort_keys``
    is set**; with empty ``sort_keys``, id = (src, dst) and re-inserting the
    same ordered pair upserts. v1.10.2 P0.3/M2: set ``sort_keys`` on structural
    edges for build idempotency (review logic C1 — **corrected by live test**:
    ``sort_keys`` alone IS sufficient; the original "needs id_strategy=
    PRIMARY_KEY" claim was wrong — edge labels have no such field).

    ``sort_keys``: edge properties whose values participate in the edge id.
    Must be a subset of ``properties``. Empty = id is just (src, dst).
    **Requires** ``frequency="MULTIPLE"`` — HugeGraph rejects ``sort_keys`` on a
    ``SINGLE``-frequency edge label (400 *"can't contain sortKeys when the
    cardinality property is single"*, live test 2026-08-05).

    ``frequency``: ``SINGLE`` (default) = at most one edge per ``(src, dst,
    label)`` pair, re-insert upserts (structural edges — idempotent without
    sort_keys). ``MULTIPLE`` = allows several edges per pair, distinguished by
    ``sort_keys`` values (relation edges — distinct ``relation_type`` values
    between the same pair stay as distinct edges, fixing the prior collapse-to-
    one data-loss bug; v1.10.2 M2/P3).
    """

    name: str
    source_label: str
    target_label: str
    properties: tuple[str, ...] = ()
    sort_keys: tuple[str, ...] = ()
    frequency: str = "SINGLE"


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
                # 实测 core 1.7.0: edge label has NO id_strategy field (vertex-only).
                # Edge id determinism comes from sort_keys alone. Never emit
                # id_strategy here — HugeGraph rejects it as unrecognized (400).
                "sort_keys": list(el.sort_keys),
                # frequency: SINGLE (default, idempotent per src/dst pair) or
                # MULTIPLE (sort_keys distinguishes multiple edges per pair).
                # sort_keys REQUIRES MULTIPLE — HugeGraph 400s otherwise.
                "frequency": el.frequency,
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
# Arrow Lake KG Schema (8 vertex labels + 14 edge labels)
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
        PropertyKeyDef("definition", "TEXT", "SINGLE"),
        PropertyKeyDef("description", "TEXT", "SINGLE"),
        PropertyKeyDef("source_chunk", "TEXT", "SET"),  # entity provenance chunk ids (v1.9.10)
    ),
    vertex_labels=(
        VertexLabelDef("document", ("id", "name"), ("id",)),
        VertexLabelDef("chunk", ("id", "content", "chunk_index"), ("id",)),
        VertexLabelDef("entity", ("name", "type", "definition", "source_chunk"), ("name",), nullable_keys=("type", "definition", "source_chunk")),
        VertexLabelDef("person", ("name",), ("name",)),
        VertexLabelDef("organization", ("name",), ("name",)),
        VertexLabelDef("location", ("name",), ("name",)),
        VertexLabelDef("concept", ("name",), ("name",)),
        VertexLabelDef("event", ("name", "date"), ("name",), nullable_keys=("date",)),
    ),
    edge_labels=(
        # Structural edges: frequency=SINGLE (default) → already idempotent.
        # Edge id = (src, dst, label); re-insert upserts (live test 2026-08-05).
        # No sort_keys needed — and sort_keys would REQUIRE MULTIPLE, which would
        # wrongly allow duplicate structural edges. Keep these SINGLE + empty.
        EdgeLabelDef("contains_chunk", "document", "chunk"),
        EdgeLabelDef("references", "chunk", "entity"),
        EdgeLabelDef("next_chunk", "chunk", "chunk"),
        # Relation edges (v1.10.2 M2/P3): frequency=MULTIPLE + sort_keys=
        # [relation_type] so distinct relations between the same entity pair
        # (e.g. A→B "uses" AND A→B "deploys") stay as separate edges. Under the
        # prior default SINGLE they collapsed to one edge (data loss). Re-
        # extracting the same (pair, relation_type) upserts → idempotent across
        # rebuilds (G3/G9). relation_type is always populated by the builder.
        EdgeLabelDef(
            "related_to", "entity", "entity",
            ("weight", "relation_type", "description"),
            sort_keys=("relation_type",), frequency="MULTIPLE",
        ),
        EdgeLabelDef(
            "part_of", "entity", "entity", ("relation_type", "description"),
            sort_keys=("relation_type",), frequency="MULTIPLE",
        ),
        # project_concept_graph verb-driven domain edges (entity→entity).
        EdgeLabelDef(
            "deployed_on", "entity", "entity", ("relation_type", "description"),
            sort_keys=("relation_type",), frequency="MULTIPLE",
        ),
        EdgeLabelDef(
            "uses", "entity", "entity", ("relation_type", "description"),
            sort_keys=("relation_type",), frequency="MULTIPLE",
        ),
        EdgeLabelDef(
            "processes", "entity", "entity", ("relation_type", "description"),
            sort_keys=("relation_type",), frequency="MULTIPLE",
        ),
        EdgeLabelDef(
            "provides", "entity", "entity", ("relation_type", "description"),
            sort_keys=("relation_type",), frequency="MULTIPLE",
        ),
        EdgeLabelDef(
            "requires", "entity", "entity", ("relation_type", "description"),
            sort_keys=("relation_type",), frequency="MULTIPLE",
        ),
        EdgeLabelDef(
            "belongs_to", "person", "organization", ("relation_type", "description"),
            sort_keys=("relation_type",), frequency="MULTIPLE",
        ),
        EdgeLabelDef(
            "located_in", "person", "location", ("relation_type", "description"),
            sort_keys=("relation_type",), frequency="MULTIPLE",
        ),
        EdgeLabelDef(
            "participates_in", "person", "event", ("relation_type", "description"),
            sort_keys=("relation_type",), frequency="MULTIPLE",
        ),
        EdgeLabelDef(
            "depicts", "event", "entity", ("relation_type", "description"),
            sort_keys=("relation_type",), frequency="MULTIPLE",
        ),
    ),
    # Index labels removed: all 8 were SECONDARY on PRIMARY_KEY fields (name/id),
    # which HugeGraph rejects — "No need to build index on properties containing
    # all primary keys" (primary keys are auto-indexed). name/id lookups remain
    # efficient via the built-in primary-key index. Previously caused 8x HTTP 400
    # at every graph creation. Add SECONDARY indexes on NON-primary-key fields
    # (e.g. entity.type) only if range/equality filtering on them is needed.
    index_labels=(),
)
