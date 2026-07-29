"""Unit tests for entity_router (v1.7.1 §4.5 — A double-write strategy)."""

from __future__ import annotations

from arrow_lake.knowledge_graph.entity_router import (
    DEFAULT_RELATION_ROUTES,
    RelationRoute,
    route_entity_type,
    route_relation,
)


# ---------------------------------------------------------------------------
# route_entity_type
# ---------------------------------------------------------------------------


def test_route_entity_type_maps_known_types() -> None:
    assert route_entity_type("person") == "person"
    assert route_entity_type("Organization") == "organization"  # case-insensitive
    assert route_entity_type("EVENT") == "event"
    assert route_entity_type("  location  ") == "location"  # stripped


def test_route_entity_type_unknown_returns_none() -> None:
    assert route_entity_type("wacky") is None
    assert route_entity_type("") is None
    assert route_entity_type(None) is None


def test_route_entity_type_project_concept_graph_domain_aliases() -> None:
    # project_concept_graph NE-like domain types route to typed labels.
    assert route_entity_type("主体") == "organization"
    assert route_entity_type("角色") == "person"
    assert route_entity_type("区域") == "location"
    # non-NE domain types stay generic (None) — no lossy forcing into NE labels.
    assert route_entity_type("软件") is None
    assert route_entity_type("硬件") is None
    assert route_entity_type("方案") is None


# ---------------------------------------------------------------------------
# route_relation — typed edge hits
# ---------------------------------------------------------------------------


def test_route_relation_belongs_to() -> None:
    assert route_relation("person", "organization", "works_at") == "belongs_to"
    assert route_relation("person", "organization", "属于") == "belongs_to"


def test_route_relation_located_in() -> None:
    assert route_relation("person", "location", "lives_in") == "located_in"
    assert route_relation("person", "location", "居住") == "located_in"


def test_route_relation_participates_in() -> None:
    assert route_relation("person", "event", "参与") == "participates_in"
    assert route_relation("person", "event", "attended") == "participates_in"


def test_route_relation_depicts_wildcard_target() -> None:
    # depicts is event→entity: any target type matches.
    assert route_relation("event", "concept", "depicts") == "depicts"
    assert route_relation("event", "organization", "描述") == "depicts"


# ---------------------------------------------------------------------------
# route_relation — fallback to related_to
# ---------------------------------------------------------------------------


def test_route_relation_unknown_synonym_falls_back() -> None:
    assert route_relation("person", "organization", "random_relation") == "related_to"


def test_route_relation_source_type_without_typed_edge_falls_back() -> None:
    # concept has no typed outgoing edge in the default table.
    assert route_relation("concept", "concept", "relates") == "related_to"


def test_route_relation_wrong_target_type_for_synonym_falls_back() -> None:
    # "works_at" only routes when target is organization.
    assert route_relation("person", "concept", "works_at") == "related_to"


def test_route_relation_empty_or_none_relation_type_falls_back() -> None:
    assert route_relation("person", "organization", "") == "related_to"
    assert route_relation("person", "organization", None) == "related_to"


# ---------------------------------------------------------------------------
# route_relation — custom routes override
# ---------------------------------------------------------------------------


def test_route_relation_custom_routes_override_default() -> None:
    custom = (
        RelationRoute(
            source_type="person",
            target_type="concept",
            synonyms=frozenset({"created", "发明"}),
            edge_label="part_of",
        ),
    )
    assert route_relation("person", "concept", "created", routes=custom) == "part_of"
    assert route_relation("person", "concept", "发明", routes=custom) == "part_of"
    # Default table not consulted when custom routes supplied.
    assert route_relation("person", "organization", "works_at", routes=custom) == "related_to"


def test_route_relation_verb_driven_domain_edges() -> None:
    # Non-NE domain types route via the "*" verb-driven routes (entity→entity).
    assert route_relation("软件", "硬件", "采用") == "uses"
    assert route_relation("软件", "硬件", "部署") == "deployed_on"
    assert route_relation("模型", "数据", "处理") == "processes"
    assert route_relation("软件", "硬件", "包含") == "part_of"
    assert route_relation("方案", "技术", "依赖") == "part_of"
    assert route_relation("主体", "交付物", "提供") == "provides"
    assert route_relation("项目", "指标", "要求") == "requires"


def test_route_relation_ne_edge_fires_for_domain_alias() -> None:
    # 角色→person, 组织→organization: the NE-specific belongs_to route wins over
    # the part_of wildcard (属于 is in both; NE route is earlier in the table).
    assert route_relation("角色", "组织", "属于") == "belongs_to"
    assert route_relation("角色", "组织", "works_at") == "belongs_to"


def test_route_relation_domain_responsible_falls_back() -> None:
    # 承担 has no route → related_to.
    assert route_relation("主体", "条款", "承担") == "related_to"


def test_default_relation_routes_cover_all_typed_edges() -> None:
    """The default table covers NE typed edges + verb-driven domain edges."""
    labels = {r.edge_label for r in DEFAULT_RELATION_ROUTES}
    assert labels == {
        "belongs_to", "located_in", "participates_in", "depicts",
        "part_of", "uses", "deployed_on", "processes", "provides", "requires",
    }
