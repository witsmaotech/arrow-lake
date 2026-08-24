"""W1.3 — shape_builder:OntologySpec → SHACL shapes Graph(MS1 F1.1)。

Semantica 生成器不做"枚举+必填+类型"这层 — 这正是自研核心。
分级用 SHACL severity 承载:枚举/必填核心字段 Violation(→reject),
warn_fields 里的字段 Warning(→warn,观察不拦)。
"""

from __future__ import annotations

from rdflib import Graph, URIRef
from rdflib.namespace import RDF, XSD

from arrow_lake.ontology.template_adapter import OntologySpec, adapt_template
from arrow_lake.ontology.shape_builder import SHACL, build_shapes, to_turtle


def _spec(**kw) -> OntologySpec:
    base = {
        "template_name": "t_demo",
        "entity_type_enum": ("主体", "项目"),
        "relation_type_enum": ("contains",),
        "type_pairs": (),
        "required_entity_fields": ("name", "type", "definition"),
        "required_relation_fields": ("source", "target", "type"),
        "entity_field_types": {"name": "str", "type": "str", "definition": "str"},
        "relation_field_types": {"source": "str", "target": "str", "type": "str"},
        "warn_fields": ("definition",),
    }
    base.update(kw)
    return OntologySpec(**base)


def _entity_property_shapes(g: Graph) -> dict[str, dict]:
    """field name → {in_list, min_count, severity, datatype} for EntityShape."""
    out: dict[str, dict] = {}
    for ps in g.subjects(RDF.type, SHACL.PropertyShape):
        owner = next(g.subjects(SHACL.property, ps), None)
        if owner is None or (owner, RDF.type, SHACL.NodeShape) not in g:
            continue
        target_class = g.value(owner, SHACL.targetClass)
        if target_class is None or "Entity" not in str(target_class):
            continue
        path = str(g.value(ps, SHACL.path)).split("#")[-1]
        in_node = g.value(ps, SHACL["in"])
        out[path] = {
            "in": [str(v) for v in g.items(in_node)] if in_node is not None else None,
            "min_count": int(g.value(ps, SHACL.minCount)) if (ps, SHACL.minCount, None) in g else 0,
            "severity": str(g.value(ps, SHACL.severity)).split("#")[-1] if (ps, SHACL.severity, None) in g else "Violation",
            "datatype": str(g.value(ps, SHACL.datatype)).split("#")[-1] if (ps, SHACL.datatype, None) in g else None,
        }
    return out


class TestBuildShapes:
    def test_enum_becomes_sh_in(self) -> None:
        g = build_shapes(_spec())
        entity = _entity_property_shapes(g)
        assert entity["type"]["in"] == ["主体", "项目"]

    def test_required_becomes_min_count(self) -> None:
        g = build_shapes(_spec())
        entity = _entity_property_shapes(g)
        assert entity["name"]["min_count"] == 1
        assert entity["type"]["min_count"] == 1

    def test_warn_field_severity_is_warning(self) -> None:
        g = build_shapes(_spec())
        entity = _entity_property_shapes(g)
        assert entity["definition"]["severity"] == "Warning"
        assert entity["name"]["severity"] == "Violation"  # 核心字段仍 reject

    def test_datatype_mapped(self) -> None:
        g = build_shapes(_spec())
        entity = _entity_property_shapes(g)
        assert entity["name"]["datatype"] == "string"

    def test_unknown_field_type_raises(self) -> None:
        spec = _spec(entity_field_types={"name": "blob", "type": "str", "definition": "str"})
        import pytest

        with pytest.raises(ValueError, match="field type"):
            build_shapes(spec)

    def test_no_enum_no_sh_in(self) -> None:
        spec = _spec(entity_type_enum=())
        g = build_shapes(spec)
        entity = _entity_property_shapes(g)
        assert entity["type"]["in"] is None  # 无枚举 → 无 sh:in,只查必填
        assert entity["type"]["min_count"] == 1

    def test_relation_shape_present(self) -> None:
        g = build_shapes(_spec())
        # Relation NodeShape 存在且 relation type 带枚举
        found = False
        for ps in g.subjects(RDF.type, SHACL.PropertyShape):
            owner = next(g.subjects(SHACL.property, ps), None)
            if owner is None:
                continue
            target_class = g.value(owner, SHACL.targetClass)
            if target_class is not None and "Relation" in str(target_class):
                path = str(g.value(ps, SHACL.path)).split("#")[-1].split("#")[-1]
                if path == "type":
                    in_node = g.value(ps, SHACL["in"])
                    assert in_node is not None and [str(v) for v in g.items(in_node)] == ["contains"]
                    found = True
        assert found, "RelationShape.type must carry sh:in"

    def test_turtle_roundtrip(self) -> None:
        g = build_shapes(_spec())
        turtle = to_turtle(g)
        g2 = Graph().parse(data=turtle, format="turtle")
        # 同构近似:三元组数一致 + 枚举值都在
        assert len(g2) == len(g)
        text = g2.serialize(format="turtle")
        assert "主体" in text and "项目" in text

    def test_turtle_roundtrip_preserves_type_pairs(self) -> None:
        """快照持久化关键契约:type-pair 元数据随 Turtle 往返不丢
        (V010 存的就是 Turtle,丢约束 = 恢复后 gate 静默降级)。"""
        from arrow_lake.ontology.validator import _pairs_from_shapes

        spec = _spec(type_pairs=(("主体", "承建", "项目"), ("项目", "*", "金额")))
        g = build_shapes(spec)
        g2 = Graph().parse(data=to_turtle(g), format="turtle")
        assert _pairs_from_shapes(g2) == (("主体", "承建", "项目"), ("项目", "*", "金额"))
