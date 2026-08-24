"""F1.5/W3.1 — ontology 段 → 模板 guideline 规则文本渲染(本体驱动抽取)。

模板 ``ontology:`` 段是唯一事实源,两个消费面:
* 校验面:``shape_builder``(SHACL,KG build 收尾门禁);
* prompt 面:本模块把段渲染成 ``guideline.rules_for_entities`` 规则行,
  在**模板生成/编排的工具路径**注入(``extraction_templates._do_generate``
  的产物增强)—— **非运行时注入**,抽取链路零改动(红线③)。

幂等契约:注入块带哨兵标记 + 段内容 hash;同内容重复 enhance 字节不变,
段内容变(hash 变)→ 重新渲染替换旧块。
"""

from __future__ import annotations

import hashlib
from typing import Any

import yaml

from arrow_lake.ontology.template_adapter import OntologySpec, adapt_template

# 注入块哨兵:规则行以固定前缀标记,重复 enhance 时先剥旧块再注新块。
_MARK = "[本体门禁]"
_HASH_KEY = "_ontology_hash"  # 哨兵行内携带的段内容 hash 锚


def render_constraint_rules(spec: OntologySpec) -> str:
    """OntologySpec → guideline 规则行文本(zh;空 spec → 空串)。

    内容三段:实体枚举 / 关系枚举 / 类型配对约束 —— 与 SHACL 校验同源,
    LLM 在抽取时即看到与门禁一致的字面约束。
    """
    if not spec.entity_type_enum and not spec.relation_type_enum and not spec.type_pairs:
        return ""
    lines: list[str] = []
    if spec.entity_type_enum:
        lines.append(
            f"{_MARK} 实体 type 必须是以下 {len(spec.entity_type_enum)} 类之一:"
            f"{'/'.join(spec.entity_type_enum)}——禁止自创值或笼统值,必须选最具体匹配"
        )
    if spec.relation_type_enum:
        lines.append(
            f"{_MARK} 关系 type 必须是以下 {len(spec.relation_type_enum)} 类之一:"
            f"{'/'.join(spec.relation_type_enum)}——禁止自创或混用英文"
        )
    if spec.type_pairs:
        by_verb: dict[str, list[str]] = {}
        for src, rel, dst in spec.type_pairs:
            if src == "*" and dst == "*":
                continue  # 全通配(不限动词)无 prompt 价值,不渲染
            key = f"{rel}:{src}→{dst}"
            by_verb.setdefault(rel, []).append(key)
        if by_verb:
            # 聚合成一行,控制规则条数(每 chunk 上下文有限)
            lines.append(
                f"{_MARK} 类型配对约束(动词:源类→目标类,不在列即为非法组合):"
                + ";".join(
                    f"{verb} 只能 {'/'.join(ks)}"
                    for verb, ks in sorted(by_verb.items())
                )
            )
    # 哨兵行:携带段内容 hash,幂等与重渲染的锚
    lines.append(f"{_MARK}{_HASH_KEY}={_spec_hash(spec)}")
    return "\n".join(lines)


def _spec_hash(spec: OntologySpec) -> str:
    payload = repr((
        spec.entity_type_enum, spec.relation_type_enum, spec.type_pairs,
        spec.warn_fields,
    ))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _strip_marked_rules(rules: list[str]) -> list[str]:
    return [r for r in rules if not (isinstance(r, str) and r.startswith(_MARK))]


def enhance_template_yaml(yaml_text: str) -> str:
    """把 ontology 段渲染进 guideline.rules_for_entities(zh+en 同步注 zh)。

    * 无 ``ontology:`` 段(或渲染为空)→ 原文返回(no-op);
    * 已注入且段 hash 未变 → 原文返回(幂等);
    * 段 hash 变 → 剥旧块注新块。
    结构保持:除 rules_for_entities.zh 外不动(sort_keys=False 保序)。
    """
    try:
        tpl: dict[str, Any] = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return yaml_text
    if not isinstance(tpl, dict) or "ontology" not in tpl:
        return yaml_text

    spec = adapt_template(tpl)
    rendered = render_constraint_rules(spec)
    rules_container = (
        ((tpl.get("guideline") or {}).get("rules_for_entities") or {}).get("zh")
    )
    if not rendered or not isinstance(rules_container, list):
        return yaml_text

    new_hash = _spec_hash(spec)
    existing = [r for r in rules_container
                if isinstance(r, str) and r.startswith(f"{_MARK}{_HASH_KEY}=")]
    if existing and existing[0] == f"{_MARK}{_HASH_KEY}={new_hash}":
        # 已注入且未变 —— 但旧块可能混在中间,规范化为「剥净再注」后的等价态:
        # 只有当前已是规范形态(旧块在尾部且规则内容一致)才字节不变。
        stripped = _strip_marked_rules(rules_container)
        expected = stripped + rendered.split("\n")
        if list(rules_container) == expected:
            return yaml_text

    stripped = _strip_marked_rules(rules_container)
    tpl["guideline"]["rules_for_entities"]["zh"] = stripped + rendered.split("\n")
    return yaml.safe_dump(tpl, allow_unicode=True, sort_keys=False, width=10_000)
