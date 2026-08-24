"""W3.1/F1.5 — shapes/ontology 段 → 模板 guideline 规则文本渲染。

本体驱动抽取的轻实现(非运行时注入,红线):模板 ``ontology:`` 段是唯一
事实源 —— shape_builder 把它变 SHACL(校验面),本模块把它渲染成
guideline.rules_for_entities 规则文本(prompt 面)。写一次段,prompt 与
校验同源,不再靠两份手写 prose 漂移。

契约:
* 渲染文本含全部枚举类(实体 + 关系 + 配对约束);
* 注入幂等:同模板内容重复 enhance 字节不变(哨兵标记 + 内容 hash);
* 模板内容变(hash 变)→ 重新渲染替换旧块;
* 无 ``ontology:`` 段的模板 → 原文返回(no-op)。
"""

from __future__ import annotations

from pathlib import Path

import yaml

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[3]
    / "arrow_lake/knowledge_graph/templates/project_concept_graph.yaml"
)


def _spec_of(yaml_text: str):
    from arrow_lake.ontology.template_adapter import adapt_template

    return adapt_template(yaml.safe_load(yaml_text))


# --- 渲染文本 ---------------------------------------------------------------


def test_render_contains_all_entity_and_relation_enums() -> None:
    from arrow_lake.ontology.rules_renderer import render_constraint_rules

    spec = _spec_of(TEMPLATE_PATH.read_text(encoding="utf-8"))
    text = render_constraint_rules(spec)
    for t in spec.entity_type_enum:
        assert t in text, f"entity enum {t} missing from rendered rules"
    for v in spec.relation_type_enum:
        assert v in text, f"relation enum {v} missing from rendered rules"
    assert "22" in text  # 枚举规模自述
    assert "16" in text


def test_render_contains_pair_constraints() -> None:
    from arrow_lake.ontology.rules_renderer import render_constraint_rules

    spec = _spec_of(TEMPLATE_PATH.read_text(encoding="utf-8"))
    text = render_constraint_rules(spec)
    # 通配动词的代表约束须可见
    assert "报价" in text and "金额" in text
    assert "训练" in text and "数据" in text


def test_render_empty_spec_returns_empty() -> None:
    from arrow_lake.ontology.rules_renderer import OntologySpec, render_constraint_rules

    assert render_constraint_rules(OntologySpec(template_name="x")) == ""


# --- enhance:注入幂等 + hash 触发重生成 --------------------------------------


def test_enrich_noop_without_ontology_section() -> None:
    from arrow_lake.ontology.rules_renderer import enhance_template_yaml

    raw = TEMPLATE_PATH.read_text(encoding="utf-8")
    tpl = yaml.safe_load(raw)
    stripped = yaml.safe_dump({k: v for k, v in tpl.items() if k != "ontology"},
                              allow_unicode=True, sort_keys=False)
    assert enhance_template_yaml(stripped) == stripped


def test_enhance_injects_block_into_guideline_rules() -> None:
    from arrow_lake.ontology.rules_renderer import enhance_template_yaml

    raw = TEMPLATE_PATH.read_text(encoding="utf-8")
    enhanced = enhance_template_yaml(raw)
    tpl = yaml.safe_load(enhanced)
    rules = tpl["guideline"]["rules_for_entities"]["zh"]
    joined = "\n".join(rules)
    assert "主体" in joined and "安全" in joined  # 22 类进规则文本
    assert any("遵循" in r and "标准" in r for r in rules)


def test_enhance_idempotent_same_content() -> None:
    from arrow_lake.ontology.rules_renderer import enhance_template_yaml

    raw = TEMPLATE_PATH.read_text(encoding="utf-8")
    once = enhance_template_yaml(raw)
    twice = enhance_template_yaml(once)
    assert once == twice, "同内容重复 enhance 必须字节不变(hash 哨兵幂等)"


def test_enhance_regenerates_when_ontology_changes() -> None:
    from arrow_lake.ontology.rules_renderer import enhance_template_yaml

    raw = TEMPLATE_PATH.read_text(encoding="utf-8")
    once = enhance_template_yaml(raw)

    tpl = yaml.safe_load(raw)
    tpl["ontology"]["entity_type_enum"] = list(tpl["ontology"]["entity_type_enum"]) + ["新类型"]
    changed = yaml.safe_dump(tpl, allow_unicode=True, sort_keys=False)
    twice = enhance_template_yaml(changed)

    assert twice != once
    joined = "\n".join(yaml.safe_load(twice)["guideline"]["rules_for_entities"]["zh"])
    assert "新类型" in joined, "ontology 变更(hash 变)必须触发规则重渲染"
    assert "23" in joined  # 枚举规模随段更新


def test_enhance_preserves_rest_of_template() -> None:
    from arrow_lake.ontology.rules_renderer import enhance_template_yaml

    raw = TEMPLATE_PATH.read_text(encoding="utf-8")
    enhanced = enhance_template_yaml(raw)
    tpl = yaml.safe_load(enhanced)
    orig = yaml.safe_load(raw)
    # 除 rules_for_entities.zh 外结构不变
    tpl["guideline"]["rules_for_entities"]["zh"] = []
    orig["guideline"]["rules_for_entities"]["zh"] = []
    assert tpl == orig


# --- 生成工具路径接线 ---------------------------------------------------------


def test_generate_flow_enhances_result(tmp_path, monkeypatch) -> None:
    """_do_generate 产物在返回前经 enhance(工具路径,非运行时注入)。"""
    import asyncio

    raw = TEMPLATE_PATH.read_text(encoding="utf-8")

    class _Req:
        prompt = "测试域"
        base = None
        doc_type = None
        sample_text = None

    async def fake_generate_fn(msgs):  # noqa: ANN001, ANN202
        return raw  # LLM 直接"生成"了一份带 ontology: 段的模板

    from arrow_lake.api.routers import extraction_templates as et

    yaml_text, errors, _ = asyncio.run(et._do_generate(_Req(), fake_generate_fn))
    assert errors == []
    rules = "\n".join(yaml.safe_load(yaml_text)["guideline"]["rules_for_entities"]["zh"])
    assert "主体" in rules, "生成产物须经 ontology→rules 增强"
