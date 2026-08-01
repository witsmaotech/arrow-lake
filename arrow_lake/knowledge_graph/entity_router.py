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

    ``target_type == "entity"`` or ``"*"`` is a wildcard matching any target
    type; ``source_type == "*"`` is a wildcard matching any source type (used
    by the verb-driven domain routes for project_concept_graph relations).
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
    # project_concept_graph domain NE aliases — parties/roles/regions map to
    # typed labels so NE typed edges (belongs_to/located_in) gain real endpoints.
    "主体": "organization", "角色": "person", "区域": "location",
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
    # --- project_concept_graph verb-driven domain edges (entity→entity).
    # source="*" matches any (possibly-routed) type; appended LAST so the
    # NE-specific routes above take priority on overlapping verbs (e.g. 属于
    # → belongs_to when source routes to person, else part_of). Endpoints
    # resolve to the generic ``entity`` vertices which always exist (double-
    # write), so these entity→entity edges never miss an endpoint.
    RelationRoute(
        source_type="*", target_type="*",
        synonyms=frozenset({"contains", "is_a", "depends_on", "integrates",
                            "包含", "属于", "依赖", "集成"}),
        edge_label="part_of",
    ),
    RelationRoute(
        source_type="*", target_type="*",
        synonyms=frozenset({"uses", "trained_on", "采用", "训练"}),
        edge_label="uses",
    ),
    RelationRoute(
        source_type="*",
        target_type="*",
        # "located_in" (en form of 部署于) overlaps the person→location route
        # above; that NE route wins for person sources, this fires otherwise.
        synonyms=frozenset({"deployed_on", "located_in", "部署", "部署于"}),
        edge_label="deployed_on",
    ),
    RelationRoute(
        source_type="*", target_type="*",
        synonyms=frozenset({"processes", "处理"}),
        edge_label="processes",
    ),
    RelationRoute(
        source_type="*", target_type="*",
        synonyms=frozenset({"provides", "提供"}),
        edge_label="provides",
    ),
    RelationRoute(
        source_type="*", target_type="*",
        synonyms=frozenset({"requires", "要求"}),
        edge_label="requires",
    ),
)


def normalize_name(name: str) -> str:
    """Normalize an entity/endpoint name for matching.

    ``casefold`` + collapse internal whitespace + strip ends. The original
    name is still stored on the vertex (``properties.name``) for display;
    this is only the lookup key, so ``"Alice"`` / ``" alice "`` / ``"ALICE"``
    resolve to the same vertex and a relation whose source/target differs
    only by case/whitespace no longer gets dropped at ``_insert_kg``.

    Conservative by design: no full/half-width conversion, no bracketed-alias
    stripping, no edit-distance/containment fuzzy match (those risk linking
    short names wrongly). Short-form vs full-form mismatches
    ("应急指挥中心" vs "市应急指挥中心") need fuzzy matching — listed as
    follow-up.
    """
    return " ".join(str(name or "").casefold().split())


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
    - ``source_type`` matches ``route.source_type`` (exact, OR wildcard when
      ``route.source_type == "*"``), AND
    - ``target_type`` matches ``route.target_type`` (exact, OR wildcard when
      ``route.target_type`` is ``"*"`` or ``"entity"``), AND
    - ``relation_type`` (lowercased) is in ``route.synonyms``.

    Domain entity types are routed to typed labels first (角色→person,
    主体→organization, ...) so NE edges fire for domain aliases; types that
    don't route keep their raw (lowercased) value and are matched by the
    ``"*"`` verb-driven domain routes.

    Args:
        source_type: The source entity's type (person/角色/软件/...).
        target_type: The target entity's type.
        relation_type: The free-text relation verb phrase.
        routes: Optional override of the synonym table (e.g. from config).

    Returns:
        A schema edge label — one of ``belongs_to``/``located_in``/
        ``participates_in``/``depicts``/``part_of``/``uses``/``deployed_on``/
        ``processes``/``provides``/``requires`` on a hit, else ``related_to``.
    """
    rt = (relation_type or "").strip().lower()
    # Route domain types to typed labels first so NE edges (belongs_to etc.)
    # fire for domain aliases (角色→person, 主体→organization, 区域→location).
    src = route_entity_type(source_type) or (source_type or "").strip().lower()
    tgt = route_entity_type(target_type) or (target_type or "").strip().lower()
    for route in routes:
        if route.source_type != "*" and src != route.source_type:
            continue
        target_ok = route.target_type in ("*", "entity") or tgt == route.target_type
        if target_ok and rt in route.synonyms:
            return route.edge_label
    return "related_to"
