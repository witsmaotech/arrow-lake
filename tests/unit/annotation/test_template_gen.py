"""W1.2 — annotation/template_gen:本体模板 → LS labeling config XML。

契约(设计 v1.1 §3 / S2):
* 实体类型枚举 → NER ``Labels name="objects"``;关系枚举 → ``Relations``;
* L4 五段:objects/events(NER)+ rules_applied(TextArea)+ scenario(Choices)
  + relations(Relations);
* events 默认按事件关键词启发式筛,可参数覆盖;scenario_choices 默认三值;
* 无 ``ontology.entity_type_enum`` 的模板(空本体)→ TemplateGenError;
* 手写 LS XML(manual_config,S2 高级覆盖)良构 → 原样透传,坏 XML → 拒绝。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from arrow_lake.annotation.template_gen import (
    LSConfig,
    TemplateGenError,
    generate_ls_config,
)

# project_concept_graph 的 ontology 段精简版(22 类/16 关系的代表子集)
TPL_FULL = """
name: demo_concept_graph
ontology:
  entity_type_enum: [主体, 组织, 项目, 指标, 告警, 事故, 金额]
  relation_type_enum: [包含, 属于, 提供, 要求]
  type_pairs:
    - [主体, 提供, 项目]
    - [项目, 要求, 指标]
"""

TPL_NO_ONTOLOGY = """
name: legacy_entity_graph
output:
  entities:
    fields:
      - {name: name, type: str, required: true}
"""

TPL_NO_RELATIONS = """
name: entities_only
ontology:
  entity_type_enum: [管段, 阀门, 调压站]
"""


class TestGenerateFromOntology:
    def test_entity_enum_becomes_objects_labels(self):
        cfg = generate_ls_config(TPL_FULL)
        labels = cfg.xml
        for etype in ("主体", "组织", "项目", "指标", "金额"):
            assert f'<Label value="{etype}"' in labels
        assert 'Labels name="objects"' in labels

    def test_relation_enum_becomes_relations_block(self):
        cfg = generate_ls_config(TPL_FULL)
        for rel in ("包含", "属于", "提供", "要求"):
            assert f'<Relation value="{rel}"' in cfg.xml

    def test_l4_five_sections_present(self):
        """objects/events Labels + Relations + TextArea + Choices + Text 全在。"""
        cfg = generate_ls_config(TPL_FULL)
        for fragment in (
            'Labels name="objects"',
            'Labels name="events"',
            '<Relations>',
            'TextArea name="rules_applied"',
            'Choices name="scenario"',
            'Text name="text" value="$text"',
        ):
            assert fragment in cfg.xml, f"missing L4 fragment: {fragment}"

    def test_xml_well_formed(self):
        cfg = generate_ls_config(TPL_FULL)
        root = ET.fromstring(cfg.xml)  # raises on malformed
        assert root.tag == "View"

    def test_events_subset_by_keyword_heuristic(self):
        """默认启发式:枚举里带事件词(告警/事故)的进 events,其余不进。"""
        cfg = generate_ls_config(TPL_FULL)
        events = cfg.l4.events_labels
        assert events == ("告警", "事故")

    def test_event_types_override(self):
        cfg = generate_ls_config(TPL_FULL, event_types=["主体", "组织"])
        assert cfg.l4.events_labels == ("主体", "组织")
        assert 'Labels name="events"' in cfg.xml

    def test_scenario_choices_default_and_override(self):
        assert generate_ls_config(TPL_FULL).l4.scenario_choices  # 非空默认
        cfg = generate_ls_config(TPL_FULL, scenario_choices=["常规", "应急"])
        assert cfg.l4.scenario_choices == ("常规", "应急")
        assert '<Choice value="应急"' in cfg.xml

    def test_relations_absent_when_template_has_none(self):
        cfg = generate_ls_config(TPL_NO_RELATIONS)
        assert cfg.l4.relations == ()
        assert "<Relations>" not in cfg.xml
        assert ET.fromstring(cfg.xml).tag == "View"


class TestValidation:
    def test_empty_ontology_rejected(self):
        with pytest.raises(TemplateGenError, match="entity_type_enum"):
            generate_ls_config(TPL_NO_ONTOLOGY)

    def test_not_a_mapping_rejected(self):
        with pytest.raises(TemplateGenError):
            generate_ls_config("- a\n- b\n")

    def test_label_special_chars_escaped(self):
        tpl = "ontology:\n  entity_type_enum: [\"a<b&c\", 普通类型]\n"
        cfg = generate_ls_config(tpl)
        root = ET.fromstring(cfg.xml)  # 转义坏了这里就抛
        values = [lb.get("value") for lb in root.iter("Label")]
        assert "a<b&c" in values


class TestManualOverride:
    def test_manual_config_passthrough(self):
        manual = '<View><Text name="text" value="$text"/></View>'
        cfg = generate_ls_config(TPL_FULL, manual_config=manual)
        assert isinstance(cfg, LSConfig)
        assert cfg.xml == manual

    def test_manual_config_malformed_rejected(self):
        with pytest.raises(TemplateGenError, match="XML"):
            generate_ls_config(TPL_FULL, manual_config="<View><unclosed>")
