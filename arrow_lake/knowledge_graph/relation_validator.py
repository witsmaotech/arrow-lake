"""Relation type-pair validation for project_concept_graph (v1.9.8 阶段1).

The project_concept_graph template constrains the relation *type* to a strict
16-value enum, but does NOT constrain which *entity types* a relation may
connect. The LLM then emits semantically meaningless edges like
``金额—训练→硬件`` or ``主体—部署于→条款``. This module post-filters those by
a white-list of legal ``(source_type, target_type)`` pairs per relation verb,
derived from the template's relation field descriptions.

Scope: ONLY applies to ``project_concept_graph`` — other templates have
different type vocabularies where this white-list is meaningless, so they pass
through unchanged. Generic whole-part relations (包含/属于/依赖) are left
unrestricted (filtering them would be over-aggressive).

Pure logic (no I/O) → fully unit-testable.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Literal

from arrow_lake.knowledge_graph.entity_router import normalize_name
from arrow_lake.knowledge_graph.extractor import ExtractionResult

logger = logging.getLogger(__name__)

_TEMPLATE_STEM = "project_concept_graph"

# Entities whose type we could not determine (template usually snaps these
# away, but guard against empty/未知 so we don't over-filter on missing info).
_UNKNOWN_TYPES = frozenset({"", "未知", "unknown"})

# v1.9.9 soft-degrade: an illegal (verb, type-pair) relation is downgraded to
# this generic marker instead of being dropped. ``相关`` is intentionally OUT
# of the template's 16-value enum, so ``route_relation("相关")`` falls through
# to the generic ``related_to`` edge label (entity→entity, always resolvable),
# keeping both endpoints connected rather than orphaning them. The original
# verb is preserved in ``properties.original_relation_type`` for traceability.
_GENERIC_VERB = "相关"
_DEGRADED_WEIGHT = 0.4

# Legal (source_type, target_type) pairs per relation verb. "*" = wildcard
# (any type on that side). Verbs NOT present here (包含/属于/依赖/...) are
# unrestricted — every pair is kept. Source/target types are the Chinese
# 22-class entity types emitted by the project_concept_graph template.
#
# Encoding follows the template's relation descriptions, e.g.:
#   部署(deployed_on 软件/模型→硬件/平台)
#   报价(priced →金额)
#   训练(trained_on 模型→数据/算法)
_WILDCARD = "*"
LEGAL_TYPE_PAIRS: dict[str, frozenset[tuple[str, str]]] = {
    "提供": frozenset({
        ("主体", "软件"), ("主体", "硬件"), ("主体", "模型"),
        ("主体", "数据"), ("主体", "服务"), ("主体", "方案"), ("主体", "项目"),
    }),
    "要求": frozenset({
        (s, t)
        for s in ("项目", "软件", "硬件", "模型", "方案")
        for t in ("要求", "指标", "资质", "标准")
    }),
    "承担": frozenset({("主体", "条款"), ("主体", "服务")}),
    "采用": frozenset({
        (s, t)
        for s in ("方案", "软件", "硬件", "模型")
        for t in ("技术", "软件", "硬件", "模型")
    }),
    "集成": frozenset({
        (s, t)
        for s in ("软件", "硬件", "模型")
        for t in ("软件", "硬件", "模型")
    }),
    "部署": frozenset({("软件", "硬件"), ("软件", "软件"), ("模型", "硬件"), ("模型", "软件")}),
    "部署于": frozenset({(_WILDCARD, "区域")}),
    "训练": frozenset({("模型", "数据"), ("模型", "模型")}),
    "处理": frozenset({("模型", "数据"), ("业务流程", "数据"), ("软件", "数据")}),
    "报价": frozenset({(_WILDCARD, "金额")}),
    "交付于": frozenset({(_WILDCARD, "节点")}),
    "达成": frozenset({(_WILDCARD, "项目"), (_WILDCARD, "指标")}),
    "遵循": frozenset({(_WILDCARD, "标准")}),
}


def is_project_concept_graph(template_path: str | None) -> bool:
    """True when ``template_path`` resolves to the project_concept_graph template.

    Accepts a full path, a bare stem, or a ``general/…`` preset string.
    """
    if not template_path:
        return False
    return Path(template_path).stem == _TEMPLATE_STEM or _TEMPLATE_STEM in template_path


def _pair_matches(pairs: frozenset[tuple[str, str]], src: str, tgt: str) -> bool:
    """True if (src, tgt) matches any legal pair, treating ``"*"`` as wildcard."""
    for p_src, p_tgt in pairs:
        src_ok = p_src == _WILDCARD or p_src == src
        tgt_ok = p_tgt == _WILDCARD or p_tgt == tgt
        if src_ok and tgt_ok:
            return True
    return False


def _is_legal(rel_type: str, src_type: str, tgt_type: str) -> bool:
    """Keep a relation unless its (types, verb) violates the white-list.

    - verb not constrained → keep (generic/whole-part relations).
    - either endpoint type unknown → keep (don't over-filter on missing info).
    - (src, tgt) not in the verb's legal pairs → drop.
    """
    pairs = LEGAL_TYPE_PAIRS.get(rel_type)
    if pairs is None:
        return True
    if src_type in _UNKNOWN_TYPES or tgt_type in _UNKNOWN_TYPES:
        return True
    return _pair_matches(pairs, src_type, tgt_type)


def filter_relations_by_type_pair(
    result: ExtractionResult, template_path: str | None,
    *,
    on_illegal: Literal["drop", "degrade"] = "drop",
) -> ExtractionResult:
    """Filter/degrade relations whose entity-type pair is illegal for their verb.

    - ``on_illegal="drop"`` (default, v1.9.8 behavior): illegal relations are
      dropped. Endpoints with no other relation go orphan.
    - ``on_illegal="degrade"`` (v1.9.9): illegal relations are KEPT but their
      ``relation_type`` is rewritten to the generic marker ``相关`` (which
      routes to a ``related_to`` edge in ``_insert_kg``), with the original
      verb preserved in ``properties.original_relation_type`` and a low
      ``properties.weight=0.4``. The connection is real (the LLM extracted it
      from source text); only the typed verb/type combo was wrong, so keeping
      the endpoints connected reduces the orphan rate without asserting a
      false specific verb.

    No-op for non-project_concept_graph templates. Returns a NEW
    :class:`ExtractionResult` (the input is never mutated). Dropped/degraded
    counts are logged so an operator can see how much the filter touched.
    """
    if not is_project_concept_graph(template_path):
        return result

    type_by_name = {
        normalize_name(e.name): (e.entity_type or "") for e in result.entities
    }
    kept: list = []
    dropped = 0
    degraded = 0
    for r in result.relations:
        src_type = type_by_name.get(normalize_name(r.source), "")
        tgt_type = type_by_name.get(normalize_name(r.target), "")
        if _is_legal(r.relation_type, src_type, tgt_type):
            kept.append(r)
        elif on_illegal == "degrade":
            # Preserve the original verb for traceability, then rewrite to the
            # generic marker + low weight. The connection stays (no orphan).
            new_props = dict(r.properties)
            new_props["original_relation_type"] = r.relation_type
            new_props["weight"] = _DEGRADED_WEIGHT
            kept.append(replace(
                r,
                relation_type=_GENERIC_VERB,
                properties=tuple(new_props.items()),
            ))
            degraded += 1
        else:
            dropped += 1

    if degraded:
        logger.info(
            "type-pair filter: degraded %d/%d relations to '%s' (template=%s)",
            degraded, len(result.relations), _GENERIC_VERB, _TEMPLATE_STEM,
        )
    if dropped:
        logger.info(
            "type-pair filter: dropped %d/%d relations (template=%s)",
            dropped, len(result.relations), _TEMPLATE_STEM,
        )
    return replace(result, relations=tuple(kept))
