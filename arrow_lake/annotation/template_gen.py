"""F4.2 — 本体模板 → Label Studio labeling config XML(v1.11.3 MS4 W1.2)。

旁路模块(红线:不进热路径)。输入是 extraction-template YAML(可信
gallery 文件),输出 LS ``label_config`` + L4 五段字段结构:

* ``objects``  ← ontology.entity_type_enum(NER Labels)
* ``events``   ← 枚举中事件类子集(关键词启发式,参数可覆盖)
* ``relations``← ontology.relation_type_enum(Relations)
* ``rules_applied`` ← TextArea(ontology_rules 编号,预标注填充)
* ``scenario`` ← Choices(默认三值,参数/契约可覆盖)

手写 LS XML(manual_config,S2 高级覆盖)良构即透传,不强制走生成器。
枚举结构化读取复用 MS1 的 :func:`ontology.template_adapter.adapt_template`
(不重 parse)。XML 经 ElementTree 构造再序列化——值自动转义;manual 校验
用 stdlib ET:expat ≥2.4 自带实体预算防护且不加载外部实体,外层再套
尺寸帽,零新依赖(version-plan 红线)。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import yaml

from arrow_lake.ontology.template_adapter import OntologySpec, adapt_template

__all__ = ["EVENT_KEYWORDS", "L4Fields", "LSConfig", "TemplateGenError", "generate_ls_config"]

# YAML 防御帽(输入虽是 gallery 文件,统一沿 actions/yaml_io 双帽纪律)
_MAX_NODES = 20_000
_MAX_DEPTH = 64
# manual_config 尺寸帽(label_config 本体就是小 XML,64KB 远超合理上限)
_MAX_MANUAL_BYTES = 64 * 1024

# events 启发式:枚举值命中任一关键词即视为事件类(设计 §3.2 "事件类")。
# 公共导出:preannotate 的实体分流(events vs objects)必须与 config 生成
# 同源,两处定义会漂移。
_EVENT_KEYWORDS = EVENT_KEYWORDS = ("告警", "事故", "事件", "故障", "预警", "风险")
# scenario 默认三值(契约 lifecycle 联动在 W2 dispatch 接,W1 先给静态默认)
_DEFAULT_SCENARIO_CHOICES = ("常规", "应急", "演练")


class TemplateGenError(ValueError):
    """模板不可转换 / manual XML 良构校验失败(路由层 → 422)。"""


@dataclass(frozen=True)
class L4Fields:
    """L4 标注字段结构(ADL 写回 schema 的来源,设计 §6.2)。"""

    objects_labels: tuple[str, ...]
    events_labels: tuple[str, ...]
    relations: tuple[str, ...]
    scenario_choices: tuple[str, ...]


@dataclass(frozen=True)
class LSConfig:
    """生成产物:LS label_config XML + L4 字段结构。"""

    xml: str
    l4: L4Fields


class _CappedLoader(yaml.SafeLoader):
    """SafeLoader + 节点数/嵌套深度双帽(沿 actions/yaml_io 同款纪律)。"""

    _node_count: int = 0
    _depth: int = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self._node_count > _MAX_NODES:
            raise ValueError("YAML rejected: too many nodes")
        self._node_count += 1
        if self._depth > _MAX_DEPTH:
            raise ValueError("YAML rejected: nesting too deep")
        self._depth += 1
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def _capped_load(text: str) -> Any:
    _CappedLoader._node_count = 0
    _CappedLoader._depth = 0
    try:
        return yaml.load(text, Loader=_CappedLoader)
    except ValueError as exc:
        raise TemplateGenError(str(exc)) from exc
    except yaml.YAMLError as exc:
        raise TemplateGenError(f"YAML rejected: {exc}") from exc


def _spec_of(template_yaml: str) -> OntologySpec:
    raw = _capped_load(template_yaml)
    if not isinstance(raw, dict):
        raise TemplateGenError("template YAML must be a mapping")
    return adapt_template(raw)


def _pick_events(entity_enum: tuple[str, ...], override: Sequence[str] | None) -> tuple[str, ...]:
    """事件类子集:显式覆盖取交集;默认按关键词启发式筛(保枚举序)。"""
    if override is not None:
        wanted = [t for t in entity_enum if t in set(override)]
        return tuple(wanted)
    return tuple(t for t in entity_enum if any(k in t for k in _EVENT_KEYWORDS))


def _build_xml(
    objects: tuple[str, ...],
    events: tuple[str, ...],
    relations: tuple[str, ...],
    scenario_choices: tuple[str, ...],
) -> str:
    view = ET.Element("View")
    if relations:
        rels = ET.SubElement(view, "Relations")
        for rel in relations:
            ET.SubElement(rels, "Relation", {"value": rel})
    objects_el = ET.SubElement(view, "Labels", {"name": "objects", "toName": "text"})
    for label in objects:
        ET.SubElement(objects_el, "Label", {"value": label})
    if events:
        events_el = ET.SubElement(view, "Labels", {"name": "events", "toName": "text"})
        for label in events:
            ET.SubElement(events_el, "Label", {"value": label})
    ET.SubElement(view, "TextArea", {"name": "rules_applied", "toName": "text", "rows": "2"})
    choices = ET.SubElement(
        view, "Choices", {"name": "scenario", "toName": "text", "choice": "single"}
    )
    for choice in scenario_choices:
        ET.SubElement(choices, "Choice", {"value": choice})
    ET.SubElement(view, "Text", {"name": "text", "value": "$text"})
    return ET.tostring(view, encoding="unicode")


def _check_manual(manual_config: str) -> str:
    """良构校验(stdlib ET;尺寸帽 + expat 内建实体预算,零依赖)。"""
    if len(manual_config.encode("utf-8")) > _MAX_MANUAL_BYTES:
        raise TemplateGenError("manual config XML rejected: too large")
    try:
        ET.fromstring(manual_config)
    except ET.ParseError as exc:
        raise TemplateGenError(f"manual config is not well-formed XML: {exc}") from exc
    return manual_config


def generate_ls_config(
    template_yaml: str,
    *,
    event_types: Sequence[str] | None = None,
    scenario_choices: Sequence[str] | None = None,
    manual_config: str | None = None,
) -> LSConfig:
    """本体模板 YAML → LS labeling config + L4 字段结构。

    manual_config 非空时走 S2 高级覆盖:良构即原样透传(L4 结构置空,
    调用方自管——手写 XML 的字段面由作者定义)。正常路径要求模板带
    ``ontology.entity_type_enum``(无枚举的存量模板先经 F1.6 补段)。
    """
    if manual_config is not None:
        return LSConfig(xml=_check_manual(manual_config), l4=L4Fields((), (), (), ()))

    spec = _spec_of(template_yaml)
    if not spec.entity_type_enum:
        raise TemplateGenError(
            "template has no ontology.entity_type_enum — formalize it first (F1.6)"
        )
    objects = spec.entity_type_enum
    events = _pick_events(objects, event_types)
    relations = spec.relation_type_enum
    scenarios = tuple(scenario_choices) if scenario_choices else _DEFAULT_SCENARIO_CHOICES
    xml = _build_xml(objects, events, relations, scenarios)
    return LSConfig(
        xml=xml,
        l4=L4Fields(
            objects_labels=objects,
            events_labels=events,
            relations=relations,
            scenario_choices=scenarios,
        ),
    )
