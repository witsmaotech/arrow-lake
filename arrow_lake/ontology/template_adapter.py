"""F1.1 — TemplateOntologyAdapter:模板 YAML → OntologySpec。

形式化的第一步:把模板携带的约束读成结构化 spec。两层数据源:

* ``ontology:`` 结构化段(显式本体,v1.11.0 起新增)— 枚举/type-pairs/
  warn 字段;
* ``output.entities/relations.fields[]`` 的 ``required``/``type``(所有存量
  模板都有)— 无 ``ontology:`` 段时降级为仅必填/类型校验。

设计决策 D1(实施计划 §1):**不 parse 字段 description 里的自然语言**
——存量模板的"22 类枚举"以 prompt 文本形态存在,靠 F1.6 把它誊写进
``ontology:`` 段,而不是让 adapter 猜。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OntologySpec:
    """机器可校验的本体约束(模板一份内容的结构化视图)。

    Attributes:
        template_name: 模板名(spec 来源标识)。
        entity_type_enum: 实体 type 枚举(去重保序;空 = 无枚举约束)。
        relation_type_enum: 关系 type 枚举(同上)。
        type_pairs: 合法 (src_type, relation, dst_type) 三元组;relation 为
            "*" 表示任意关系(2 元对的占位)。空 = 不做跨实体约束。
        required_entity_fields / required_relation_fields: 必填字段名。
        entity_field_types / relation_field_types: 字段名 → 原始类型字面量
            (str/int/float/bool,shape_builder 再映射 xsd)。
        warn_fields: 只告警不拒绝的字段(校验分级 warn 级,如 definition)。
    """

    template_name: str
    entity_type_enum: tuple[str, ...] = ()
    relation_type_enum: tuple[str, ...] = ()
    type_pairs: tuple[tuple[str, str, str], ...] = ()
    required_entity_fields: tuple[str, ...] = ()
    required_relation_fields: tuple[str, ...] = ()
    entity_field_types: dict[str, str] = field(default_factory=dict)
    relation_field_types: dict[str, str] = field(default_factory=dict)
    warn_fields: tuple[str, ...] = ()


def _dedup_keep_order(values: Any) -> tuple[str, ...]:
    """枚举去重且保序(dict.fromkeys 语义,输入宽容为 list/str/None)。"""
    if not values:
        return ()
    if isinstance(values, str):
        values = [values]
    return tuple(dict.fromkeys(str(v) for v in values))


def _required_and_types(section: dict[str, Any] | None) -> tuple[tuple[str, ...], dict[str, str]]:
    """从 output.entities/relations.fields 推必填字段与字段类型。"""
    if not section:
        return (), {}
    fields = section.get("fields") or []
    required = tuple(f["name"] for f in fields if f.get("required"))
    types = {f["name"]: str(f.get("type", "str")) for f in fields if f.get("name")}
    return required, types


def _normalize_pairs(raw: Any) -> tuple[tuple[str, str, str], ...]:
    """type_pairs 元素规范化:2 元 [src,dst] 补 '*';其余长度拒绝。"""
    if not raw:
        return ()
    out: list[tuple[str, str, str]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 3:
            out.append((str(item[0]), str(item[1]), str(item[2])))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            out.append((str(item[0]), "*", str(item[1])))
        else:
            raise ValueError(
                f"type_pairs entries must be [src, rel, dst] or [src, dst], got: {item!r}"
            )
    return tuple(out)


def adapt_template(template: dict[str, Any]) -> OntologySpec:
    """从模板 dict( yaml.safe_load 的结果)构建 OntologySpec。"""
    entities = (template.get("output") or {}).get("entities") or {}
    relations = (template.get("output") or {}).get("relations") or {}
    req_e, types_e = _required_and_types(entities)
    req_r, types_r = _required_and_types(relations)

    onto = template.get("ontology") or {}
    # ontology 段可显式覆盖必填(缺省回落到 fields 的 required 推导)
    required_entities = _dedup_keep_order(onto.get("required_entity_fields")) or req_e
    required_relations = _dedup_keep_order(onto.get("required_relation_fields")) or req_r

    return OntologySpec(
        template_name=str(template.get("name", "")),
        entity_type_enum=_dedup_keep_order(onto.get("entity_type_enum")),
        relation_type_enum=_dedup_keep_order(onto.get("relation_type_enum")),
        type_pairs=_normalize_pairs(onto.get("type_pairs")),
        required_entity_fields=required_entities,
        required_relation_fields=required_relations,
        entity_field_types=types_e,
        relation_field_types=types_r,
        warn_fields=_dedup_keep_order(onto.get("warn_fields")),
    )
