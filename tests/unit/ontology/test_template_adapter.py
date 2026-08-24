"""W1.2 — template_adapter:模板 YAML → OntologySpec(MS1 F1.1)。

形式化第一步:把模板的隐式约束(ontology: 结构化段,或 fields 的
required/type)读成可校验的 OntologySpec。无 ontology: 段时降级为仅
必填/类型(不 parse description 自然语言 — D1)。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arrow_lake.ontology.template_adapter import OntologySpec, adapt_template

_TEMPLATE_DIR = Path(__file__).parents[3] / "arrow_lake" / "knowledge_graph" / "templates"


def _full_template() -> dict:
    return {
        "name": "t_demo",
        "output": {
            "entities": {
                "fields": [
                    {"name": "name", "type": "str", "required": True},
                    {"name": "type", "type": "str", "required": True},
                    {"name": "definition", "type": "str", "required": True},
                ]
            },
            "relations": {
                "fields": [
                    {"name": "source", "type": "str", "required": True},
                    {"name": "target", "type": "str", "required": True},
                    {"name": "type", "type": "str", "required": True},
                ]
            },
        },
        "ontology": {
            "entity_type_enum": ["主体", "项目", "主体"],  # 重复 → 去重保序
            "relation_type_enum": ["contains", "is_a"],
            "type_pairs": [["主体", "承建", "项目"], ["项目", "包含"]],  # 2/3 元混合
            "warn_fields": ["definition"],
        },
    }


class TestAdaptTemplate:
    def test_full_ontology_section(self) -> None:
        spec = adapt_template(_full_template())
        assert isinstance(spec, OntologySpec)
        assert spec.template_name == "t_demo"
        assert spec.entity_type_enum == ("主体", "项目")  # 去重保序
        assert spec.relation_type_enum == ("contains", "is_a")
        # 2 元对补 "*" 关系占位,3 元原样
        assert spec.type_pairs == (("主体", "承建", "项目"), ("项目", "*", "包含"))
        assert spec.required_entity_fields == ("name", "type", "definition")
        assert spec.warn_fields == ("definition",)

    def test_degrades_without_ontology_section(self) -> None:
        """无 ontology: 段 → 仅必填/类型约束(存量模板兼容,D1)。"""
        tpl = _full_template()
        del tpl["ontology"]
        spec = adapt_template(tpl)
        assert spec.entity_type_enum == ()
        assert spec.relation_type_enum == ()
        assert spec.type_pairs == ()
        assert spec.warn_fields == ()
        # required 仍从 fields 推导
        assert spec.required_entity_fields == ("name", "type", "definition")
        assert spec.entity_field_types == {"name": "str", "type": "str", "definition": "str"}

    def test_enum_dedup_preserves_order(self) -> None:
        tpl = _full_template()
        tpl["ontology"]["entity_type_enum"] = ["b", "a", "b", "c", "a"]
        spec = adapt_template(tpl)
        assert spec.entity_type_enum == ("b", "a", "c")

    def test_non_required_fields_not_in_required(self) -> None:
        tpl = _full_template()
        tpl["output"]["entities"]["fields"].append(
            {"name": "confidence", "type": "float", "required": False}
        )
        spec = adapt_template(tpl)
        assert "confidence" not in spec.required_entity_fields
        assert spec.entity_field_types["confidence"] == "float"

    def test_real_project_concept_graph_degrades(self) -> None:
        """真实模板(尚无 ontology: 段)必须可降级解析 — F1.6 补段前的基线。"""
        path = _TEMPLATE_DIR / "project_concept_graph.yaml"
        tpl = yaml.safe_load(path.read_text(encoding="utf-8"))
        spec = adapt_template(tpl)
        assert spec.template_name == "project_concept_graph"
        assert spec.entity_type_enum == ()  # 降级:枚举藏在 description 自然语言里
        assert {"name", "type", "definition"} <= set(spec.required_entity_fields)

    def test_minimal_template(self) -> None:
        """只有 name 的空模板 → 空 spec,不抛(最大兼容)。"""
        spec = adapt_template({"name": "empty"})
        assert spec.template_name == "empty"
        assert spec.required_entity_fields == ()

    def test_type_pair_malformed_raises(self) -> None:
        tpl = _full_template()
        tpl["ontology"]["type_pairs"] = [["only-one"]]
        with pytest.raises(ValueError, match="type_pairs"):
            adapt_template(tpl)
