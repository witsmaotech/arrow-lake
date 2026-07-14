"""Entity type → vertex label + relation → edge label routing (v1.7.1 §4.5).

Routes extracted entities/relations onto the HugeGraph schema's typed labels:

- **Entities** are written to BOTH the generic ``entity`` label (keeps
  ``references`` / ``related_to`` edges intact across the schema's single-type
  edge endpoints) AND a typed label (person/organization/location/concept/
  event) when the type is recognized — the "double-write" strategy.

- **Relations** are matched against a synonym table; on hit, a typed edge label
  (``belongs_to`` / ``located_in`` / ``participates_in`` / ``depicts``) is
  emitted and the builder uses the typed vertices as endpoints; otherwise the
  generic ``related_to`` is used with the generic entity vertices.

This module is pure (no I/O) so it is fully unit-testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelationRoute:
    """A relation routing rule: a type pair + synonym set → an edge label.

    ``target_type == "entity"`` is a wildcard matching any target entity type
    (used by ``depicts`` which is ``event → entity`` in the schema).
    """

    source_type: str
    target_type: str
    synonyms: frozenset[str]
    edge_label: str


# entity_type → typed vertex label. Unmapped types (incl. all concept-like
# types: 概念/核心概念/属性/架构组件/...) stay on the generic ``entity`` label
# only — NO typed double-write. This eliminates the prior 418-isolated-concept
# -vertex problem where every concept_graph entity was also written to a
# ``concept`` vertex that no edge ever referenced.
#
# Only structurally-distinct types (person/org/location/event) get a typed
# vertex, so typed edges (belongs_to/located_in/...) have real endpoints.
# CJK aliases are recognized since LLM-extracted types are Chinese by default.
_ENTITY_TYPE_LABELS: dict[str, str] = {
    "person": "person", "人物": "person", "人": "person",
    "organization": "organization", "组织": "organization",
    "机构": "organization", "公司": "organization", "单位": "organization",
    "location": "location", "地点": "location", "位置": "location", "地方": "location",
    "event": "event", "事件": "event",
}


DEFAULT_RELATION_ROUTES: tuple[RelationRoute, ...] = (
    RelationRoute(
        source_type="person",
        target_type="organization",
        synonyms=frozenset({
            "works_at", "employed_by", "works_for",
            "属于", "就职于", "工作于",
            "founder_of", "ceo_of", "owner_of",
        }),
        edge_label="belongs_to",
    ),
    RelationRoute(
        source_type="person",
        target_type="location",
        synonyms=frozenset({
            "lives_in", "lives_at", "located_in",
            "位于", "居住", "来自", "from",
        }),
        edge_label="located_in",
    ),
    RelationRoute(
        source_type="person",
        target_type="event",
        synonyms=frozenset({
            "participated", "participated_in", "attended",
            "参与", "参加",
        }),
        edge_label="participates_in",
    ),
    RelationRoute(
        source_type="event",
        target_type="entity",  # wildcard: any target type
        synonyms=frozenset({
            "depicts", "describes", "about",
            "描述", "记录", "主题",
        }),
        edge_label="depicts",
    ),
)


def route_entity_type(entity_type: str | None) -> str | None:
    """Map an entity_type to a typed vertex label, or ``None`` (generic only)."""
    return _ENTITY_TYPE_LABELS.get((entity_type or "").strip().lower())


def route_relation(
    source_type: str | None,
    target_type: str | None,
    relation_type: str | None,
    routes: tuple[RelationRoute, ...] = DEFAULT_RELATION_ROUTES,
) -> str:
    """Return the schema edge label for a relation, defaulting to ``related_to``.

    A route matches when:
    - ``source_type`` equals ``route.source_type`` (exact), AND
    - ``target_type`` matches ``route.target_type`` (exact, OR wildcard when
      ``route.target_type == "entity"``), AND
    - ``relation_type`` (lowercased) is in ``route.synonyms``.

    Args:
        source_type: The source entity's normalized type (person/...).
        target_type: The target entity's normalized type.
        relation_type: The free-text relation verb phrase.
        routes: Optional override of the synonym table (e.g. from config).

    Returns:
        A schema edge label — one of ``belongs_to``/``located_in``/
        ``participates_in``/``depicts`` on a hit, else ``related_to``.
    """
    rt = (relation_type or "").strip().lower()
    src = (source_type or "").strip().lower()
    tgt = (target_type or "").strip().lower()
    for route in routes:
        if src != route.source_type:
            continue
        target_ok = route.target_type == "entity" or tgt == route.target_type
        if target_ok and rt in route.synonyms:
            return route.edge_label
    return "related_to"
