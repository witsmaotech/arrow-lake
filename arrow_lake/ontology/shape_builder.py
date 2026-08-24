"""F1.1 — ShapeBuilder:OntologySpec → SHACL shapes Graph。

自研核心:Semantica 的生成器不做"枚举 + 必填 + 类型"这层 PropertyShape,
恰是 KG 门禁要的。分级契约:

* 枚举(sh:in)与核心必填 → ``sh:Violation``(validator 映射 reject);
* ``spec.warn_fields`` 里的字段 → ``sh:Warning``(映射 warn,观察不拦)。

``type_pairs`` 跨实体约束不进 SHACL(表达笨重且 pyshacl 慢)— 由
validator 的纯 Python 配对检查承担(见 validator.py)。
"""

from __future__ import annotations

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, XSD

from arrow_lake.ontology.template_adapter import OntologySpec

AL = Namespace("https://arrow-lake.dev/ontology#")
# rdflib 7 顶层无 SHACL namespace 类(pyshacl 用内部 SH_* 常量)— vocab URI
# 是 W3C 固定值,自建等价。pyshacl 结果图同 URI,序列化/解析互通。
SHACL = Namespace("http://www.w3.org/ns/shacl#")

_XSD_TYPES: dict[str, URIRef] = {
    "str": XSD.string,
    "string": XSD.string,
    "int": XSD.integer,
    "integer": XSD.integer,
    "float": XSD.double,
    "double": XSD.double,
    "bool": XSD.boolean,
    "boolean": XSD.boolean,
}


def _datatype_for(field_type: str) -> URIRef:
    try:
        return _XSD_TYPES[field_type.lower()]
    except KeyError:
        raise ValueError(
            f"Unsupported field type '{field_type}' "
            f"(expected one of: {', '.join(sorted(_XSD_TYPES))})"
        ) from None


def _add_property_shape(
    g: Graph,
    node_shape: URIRef,
    *,
    path: str,
    severity: URIRef,
    min_count: int = 1,
    enum: tuple[str, ...] = (),
    datatype: str | None = None,
) -> None:
    ps = BNode()
    g.add((ps, RDF.type, SHACL.PropertyShape))
    g.add((ps, SHACL.path, AL[path]))
    g.add((ps, SHACL.severity, severity))
    g.add((ps, SHACL.minCount, Literal(min_count)))
    if datatype is not None:
        g.add((ps, SHACL.datatype, _datatype_for(datatype)))
    if enum:
        head = BNode()
        Collection(g, head, [Literal(v) for v in enum])
        g.add((ps, SHACL["in"], head))
    g.add((node_shape, SHACL.property, ps))


def _build_node_shape(
    g: Graph,
    *,
    shape_name: str,
    target_class: str,
    spec: OntologySpec,
    required_fields: tuple[str, ...],
    field_types: dict[str, str],
    enum_field: str | None,
    enum_values: tuple[str, ...],
) -> URIRef:
    node_shape = AL[shape_name]
    g.add((node_shape, RDF.type, SHACL.NodeShape))
    g.add((node_shape, SHACL.targetClass, AL[target_class]))

    warn = set(spec.warn_fields)
    for name in required_fields:
        severity = SHACL.Warning if name in warn else SHACL.Violation
        _add_property_shape(
            g, node_shape,
            path=name, severity=severity,
            datatype=field_types.get(name, "str"),
            enum=enum_values if name == enum_field and enum_values else (),
        )
    # 枚举字段不在 required 里时也单独挂 sh:in(出现即校验)
    if enum_field and enum_values and enum_field not in required_fields:
        _add_property_shape(
            g, node_shape,
            path=enum_field, severity=SHACL.Violation,
            min_count=0, enum=enum_values,
            datatype=field_types.get(enum_field, "str"),
        )
    return node_shape


def build_shapes(spec: OntologySpec) -> Graph:
    """OntologySpec → SHACL shapes graph(EntityShape + RelationShape)。

    type_pairs 以 ``al:TypePairs al:pair "src\\x1frel\\x1fdst"`` 元数据三元组
    编码进同一 graph — Turtle 快照持久化后 validator 仍能读回(不进 SHACL
    约束,配对检查由 validator 纯 Python 承担)。
    """
    g = Graph()
    g.bind("al", AL)
    g.bind("sh", SHACL)
    g.bind("xsd", XSD)

    _build_node_shape(
        g,
        shape_name="EntityShape",
        target_class="Entity",
        spec=spec,
        required_fields=spec.required_entity_fields,
        field_types=spec.entity_field_types,
        enum_field="type",
        enum_values=spec.entity_type_enum,
    )
    _build_node_shape(
        g,
        shape_name="RelationShape",
        target_class="Relation",
        spec=spec,
        required_fields=spec.required_relation_fields,
        field_types=spec.relation_field_types,
        enum_field="type",
        enum_values=spec.relation_type_enum,
    )
    for src, rel, dst in spec.type_pairs:
        g.add((AL.TypePairs, AL.pair, Literal("\x1f".join((src, rel, dst)))))
    return g


def to_turtle(g: Graph) -> str:
    """Graph → Turtle 文本(ontology_versions.shapes_turtle 的存储形态)。"""
    return g.serialize(format="turtle")
