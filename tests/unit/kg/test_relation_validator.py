"""Unit tests for relation_validator (project_concept_graph type-pair filter).

Pure-logic: no LLM, no HugeGraph. The filter keeps relations whose
(source_type, relation, target_type) is on the project_concept_graph
white-list, drops illegal ones (e.g. 金额—训练→硬件), leaves generic
relations (包含/属于/依赖) unrestricted, and is a no-op for other templates.
"""

from __future__ import annotations

from arrow_lake.knowledge_graph.extractor import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from arrow_lake.knowledge_graph.relation_validator import (
    filter_relations_by_type_pair,
    is_project_concept_graph,
)

_PCG = "arrow_lake/knowledge_graph/templates/project_concept_graph.yaml"


def _e(name: str, etype: str) -> ExtractedEntity:
    return ExtractedEntity(name=name, entity_type=etype, properties=())


def _result(entities, relations) -> ExtractionResult:
    return ExtractionResult(entities=tuple(entities), relations=tuple(relations), raw_text="")


def _rel(s, t, rt) -> ExtractedRelation:
    return ExtractedRelation(source=s, target=t, relation_type=rt, properties=())


def test_drops_illegal_type_pair() -> None:
    # 训练 legal = 模型→{数据,模型}; 金额→硬件 is illegal → dropped.
    r = _result(
        [_e("M", "金额"), _e("H", "硬件")],
        [_rel("M", "H", "训练")],
    )
    out = filter_relations_by_type_pair(r, _PCG)
    assert out.relations == ()


def test_keeps_legal_type_pair() -> None:
    # 训练: 模型→数据 legal.
    r = _result(
        [_e("M", "模型"), _e("D", "数据")],
        [_rel("M", "D", "训练")],
    )
    out = filter_relations_by_type_pair(r, _PCG)
    assert len(out.relations) == 1


def test_wildcard_source_matches() -> None:
    # 部署于: *→区域; 软件→区域 legal.
    r = _result(
        [_e("S", "软件"), _e("R", "区域")],
        [_rel("S", "R", "部署于")],
    )
    out = filter_relations_by_type_pair(r, _PCG)
    assert len(out.relations) == 1


def test_wildcard_target_matches() -> None:
    # 报价: *→金额; 主体→金额 legal.
    r = _result(
        [_e("P", "主体"), _e("A", "金额")],
        [_rel("P", "A", "报价")],
    )
    out = filter_relations_by_type_pair(r, _PCG)
    assert len(out.relations) == 1


def test_wildcard_target_violated() -> None:
    # 报价: *→金额; 主体→硬件 illegal.
    r = _result(
        [_e("P", "主体"), _e("H", "硬件")],
        [_rel("P", "H", "报价")],
    )
    out = filter_relations_by_type_pair(r, _PCG)
    assert out.relations == ()


def test_generic_relation_unrestricted() -> None:
    # 包含 is not in LEGAL_TYPE_PAIRS → any type pair kept.
    r = _result(
        [_e("P", "主体"), _e("A", "金额")],
        [_rel("P", "A", "包含")],
    )
    out = filter_relations_by_type_pair(r, _PCG)
    assert len(out.relations) == 1


def test_non_project_template_is_noop() -> None:
    # entity_graph → no filtering applied at all.
    r = _result(
        [_e("M", "金额"), _e("H", "硬件")],
        [_rel("M", "H", "训练")],
    )
    out = filter_relations_by_type_pair(r, "entity_graph")
    assert out.relations == r.relations


def test_unknown_entity_type_kept_not_overfiltered() -> None:
    # Missing/empty type → keep (don't drop when type inference is absent).
    r = _result(
        [_e("M", ""), _e("H", "硬件")],
        [_rel("M", "H", "训练")],
    )
    out = filter_relations_by_type_pair(r, _PCG)
    assert len(out.relations) == 1


def test_mixed_batch_keeps_legal_drops_illegal() -> None:
    r = _result(
        [_e("M", "模型"), _e("D", "数据"), _e("X", "金额"), _e("H", "硬件")],
        [
            _rel("M", "D", "训练"),   # legal
            _rel("X", "H", "训练"),   # illegal
            _rel("M", "D", "包含"),   # generic, kept
        ],
    )
    out = filter_relations_by_type_pair(r, _PCG)
    types = [(rel.source, rel.target, rel.relation_type) for rel in out.relations]
    assert ("M", "D", "训练") in types
    assert ("M", "D", "包含") in types
    assert ("X", "H", "训练") not in types
    assert len(out.relations) == 2


def test_is_project_concept_graph_detects_stem_and_path() -> None:
    assert is_project_concept_graph(_PCG)
    assert is_project_concept_graph("project_concept_graph")
    assert is_project_concept_graph("/some/dir/project_concept_graph.yaml")
    assert not is_project_concept_graph("entity_graph")
    assert not is_project_concept_graph("concept_graph")


def test_original_result_not_mutated() -> None:
    r = _result(
        [_e("X", "金额"), _e("H", "硬件")],
        [_rel("X", "H", "训练")],
    )
    before = r.relations
    filter_relations_by_type_pair(r, _PCG)
    assert r.relations == before  # immutable: original untouched


# --- v1.9.9 soft-degrade: illegal type-pair → generic 相关 (not dropped) ---


def _props(rel: ExtractedRelation) -> dict[str, object]:
    return dict(rel.properties)


def test_degrade_keeps_relation_as_generic() -> None:
    # 训练 金额→硬件 illegal; degrade keeps it, rewrites verb to 相关.
    r = _result(
        [_e("X", "金额"), _e("H", "硬件")],
        [_rel("X", "H", "训练")],
    )
    out = filter_relations_by_type_pair(r, _PCG, on_illegal="degrade")
    assert len(out.relations) == 1
    rel = out.relations[0]
    assert rel.relation_type == "相关"
    p = _props(rel)
    assert p["original_relation_type"] == "训练"
    assert p["weight"] == 0.4


def test_degrade_default_is_drop() -> None:
    # No on_illegal arg → drop (v1.9.8 backward-compat default unchanged).
    r = _result(
        [_e("X", "金额"), _e("H", "硬件")],
        [_rel("X", "H", "训练")],
    )
    out = filter_relations_by_type_pair(r, _PCG)
    assert out.relations == ()


def test_drop_mode_explicit_matches_default() -> None:
    r = _result(
        [_e("X", "金额"), _e("H", "硬件")],
        [_rel("X", "H", "训练")],
    )
    out = filter_relations_by_type_pair(r, _PCG, on_illegal="drop")
    assert out.relations == ()


def test_degrade_preserves_description() -> None:
    # An existing description property survives alongside the added markers.
    rel = ExtractedRelation(
        source="X", target="H", relation_type="训练",
        properties=(("description", "金额用来训练硬件"),),
    )
    r = _result([_e("X", "金额"), _e("H", "硬件")], [rel])
    out = filter_relations_by_type_pair(r, _PCG, on_illegal="degrade")
    p = _props(out.relations[0])
    assert p["description"] == "金额用来训练硬件"
    assert p["original_relation_type"] == "训练"
    assert p["weight"] == 0.4


def test_degrade_mixed_batch() -> None:
    # legal kept as-is; illegal degraded to 相关; generic (包含) kept unchanged.
    r = _result(
        [_e("M", "模型"), _e("D", "数据"), _e("X", "金额"), _e("H", "硬件")],
        [
            _rel("M", "D", "训练"),   # legal
            _rel("X", "H", "训练"),   # illegal → degraded
            _rel("M", "D", "包含"),   # generic, kept as-is
        ],
    )
    out = filter_relations_by_type_pair(r, _PCG, on_illegal="degrade")
    by_triple = {(rel.source, rel.target, rel.relation_type): rel for rel in out.relations}
    # legal retained with original verb
    assert ("M", "D", "训练") in by_triple
    # generic retained with original verb
    assert ("M", "D", "包含") in by_triple
    # illegal downgraded to 相关 (endpoints preserved → no orphan)
    assert ("X", "H", "相关") in by_triple
    assert _props(by_triple[("X", "H", "相关")])["original_relation_type"] == "训练"
    assert len(out.relations) == 3


def test_degraded_relation_passes_second_filter() -> None:
    # 相关 is not in LEGAL_TYPE_PAIRS → unrestricted → survives a re-filter.
    r = _result(
        [_e("X", "金额"), _e("H", "硬件")],
        [_rel("X", "H", "训练")],
    )
    once = filter_relations_by_type_pair(r, _PCG, on_illegal="degrade")
    twice = filter_relations_by_type_pair(once, _PCG, on_illegal="degrade")
    assert len(twice.relations) == 1
    assert twice.relations[0].relation_type == "相关"


def test_degrade_non_project_template_is_noop() -> None:
    # Non-project template → no filtering/degrading at all (passes through).
    r = _result(
        [_e("X", "金额"), _e("H", "硬件")],
        [_rel("X", "H", "训练")],
    )
    out = filter_relations_by_type_pair(r, "entity_graph", on_illegal="degrade")
    assert out.relations == r.relations
