"""Unit tests for orphan_linker (v1.9.9 heuristic orphan-vertex linking).

Pure logic: no LLM, no HugeGraph, no I/O. Embeddings are a plain
``dict[str, list[float]]`` keyed by normalized name. An orphan entity (in NO
surviving relation) is linked to a co-occurring connected entity only when
they share a chunk (evidence gate), embedding cosine ≥ threshold, AND a legal
type-pair verb exists. Honors the project rule "不创建隐含的常识性关联" —
without co-occurrence evidence, no link is fabricated.
"""

from __future__ import annotations

from arrow_lake.knowledge_graph.entity_router import normalize_name
from arrow_lake.knowledge_graph.extractor import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)

_PCG = "arrow_lake/knowledge_graph/templates/project_concept_graph.yaml"


def _e(name: str, etype: str) -> ExtractedEntity:
    return ExtractedEntity(name=name, entity_type=etype, properties=())


def _result(entities, relations) -> ExtractionResult:
    return ExtractionResult(entities=tuple(entities), relations=tuple(relations), raw_text="")


def _rel(s: str, t: str, rt: str, props=()) -> ExtractedRelation:
    return ExtractedRelation(source=s, target=t, relation_type=rt, properties=props)


def _emb(d: dict[str, list[float]]) -> dict[str, list[float]]:
    return {normalize_name(k): v for k, v in d.items()}


def _link(result, entity_chunks, emb, **kw):
    from arrow_lake.knowledge_graph.orphan_linker import link_orphans

    defaults = dict(template_path=_PCG, threshold=0.7, max_partners=3, max_links=10)
    defaults.update(kw)
    return link_orphans(result, entity_chunks, _emb(emb), **defaults)


# ---------------------------------------------------------------------------
# Happy path + gates
# ---------------------------------------------------------------------------


def test_links_orphan_to_co_occurring_connected_entity() -> None:
    # 甲方—提供→平台 connects 甲方/平台; 响应时间 is orphan, co-occurs in c1.
    # 指标↔软件: forward no verb, reverse 软件→指标 = 要求 legal → link 平台→响应时间.
    result = _result(
        [_e("甲方", "主体"), _e("平台", "软件"), _e("响应时间", "指标")],
        [_rel("甲方", "平台", "提供")],
    )
    ec = {"甲方": ["c1"], "平台": ["c1"], "响应时间": ["c1"]}
    emb = {"响应时间": [1.0, 0.0], "平台": [0.8, 0.6], "甲方": [0.0, 1.0]}
    new_result, new_rels = _link(result, ec, emb)
    assert len(new_rels) == 1
    r = new_rels[0]
    # reverse direction: src = candidate (平台), tgt = orphan (响应时间)
    assert r.source == "平台"
    assert r.target == "响应时间"
    assert r.relation_type == "要求"
    p = dict(r.properties)
    assert p["weight"] == 0.4
    assert p["inferred"] is True
    # spliced into the returned result
    assert len(new_result.relations) == 2


def test_forward_direction_picks_forward_verb() -> None:
    # orphan 主体 links to connected 软件: 主体→软件 提供 legal forward.
    result = _result(
        [_e("平台", "软件"), _e("乙方", "主体"), _e("系统", "软件")],
        [_rel("平台", "系统", "集成")],  # connects 平台/系统; 乙方 orphan
    )
    ec = {"平台": ["c1"], "系统": ["c1"], "乙方": ["c1"]}
    emb = {"乙方": [1.0, 0.0], "平台": [0.8, 0.6], "系统": [0.0, 1.0]}
    _, new_rels = _link(result, ec, emb)
    assert len(new_rels) == 1
    r = new_rels[0]
    assert r.source == "乙方"            # forward: src = orphan
    assert r.target == "平台"
    assert r.relation_type == "提供"


def test_co_occurrence_gate_blocks_link() -> None:
    # orphan alone in a different chunk → no co-occurring connected candidate.
    result = _result(
        [_e("甲方", "主体"), _e("平台", "软件"), _e("响应时间", "指标")],
        [_rel("甲方", "平台", "提供")],
    )
    ec = {"甲方": ["c1"], "平台": ["c1"], "响应时间": ["c2"]}  # 响应时间 alone in c2
    emb = {"响应时间": [1.0, 0.0], "平台": [0.8, 0.6], "甲方": [0.0, 1.0]}
    _, new_rels = _link(result, ec, emb)
    assert new_rels == []


def test_threshold_gate_blocks_link() -> None:
    # sim(响应时间, 平台) ≈ 0.1 < 0.7 → no link despite co-occurrence + legal verb.
    result = _result(
        [_e("甲方", "主体"), _e("平台", "软件"), _e("响应时间", "指标")],
        [_rel("甲方", "平台", "提供")],
    )
    ec = {"甲方": ["c1"], "平台": ["c1"], "响应时间": ["c1"]}
    emb = {"响应时间": [1.0, 0.0], "平台": [0.1, 0.99], "甲方": [0.0, 1.0]}
    _, new_rels = _link(result, ec, emb)
    assert new_rels == []


def test_no_legal_verb_no_link() -> None:
    # 主体↔主体 has no legal verb in LEGAL_TYPE_PAIRS → no link.
    result = _result(
        [_e("甲方", "主体"), _e("平台", "软件"), _e("监理方", "主体")],
        [_rel("甲方", "平台", "提供")],  # 监理方 orphan, co-occurs with 甲方
    )
    ec = {"甲方": ["c1"], "平台": ["c1"], "监理方": ["c1"]}
    emb = {"监理方": [1.0, 0.0], "甲方": [0.9, 0.4], "平台": [0.0, 1.0]}
    _, new_rels = _link(result, ec, emb)
    assert new_rels == []


def test_unknown_type_no_link() -> None:
    # orphan with empty type → _pick_legal_verb returns None → no link.
    result = _result(
        [_e("甲方", "主体"), _e("平台", "软件"), _e("某物", "")],
        [_rel("甲方", "平台", "提供")],
    )
    ec = {"甲方": ["c1"], "平台": ["c1"], "某物": ["c1"]}
    emb = {"某物": [1.0, 0.0], "平台": [0.9, 0.4], "甲方": [0.0, 1.0]}
    _, new_rels = _link(result, ec, emb)
    assert new_rels == []


# ---------------------------------------------------------------------------
# No-op guards
# ---------------------------------------------------------------------------


def test_no_orphans_noop() -> None:
    result = _result(
        [_e("甲方", "主体"), _e("平台", "软件")],
        [_rel("甲方", "平台", "提供")],
    )
    ec = {"甲方": ["c1"], "平台": ["c1"]}
    emb = {"甲方": [1.0, 0.0], "平台": [0.8, 0.6]}
    new_result, new_rels = _link(result, ec, emb)
    assert new_rels == []
    assert new_result.relations == result.relations


def test_single_entity_noop() -> None:
    result = _result([_e("甲方", "主体")], [])
    _, new_rels = _link(result, {"甲方": ["c1"]}, {"甲方": [1.0, 0.0]})
    assert new_rels == []


def test_non_project_template_noop() -> None:
    result = _result(
        [_e("甲方", "主体"), _e("平台", "软件"), _e("响应时间", "指标")],
        [_rel("甲方", "平台", "提供")],
    )
    ec = {"甲方": ["c1"], "平台": ["c1"], "响应时间": ["c1"]}
    emb = {"响应时间": [1.0, 0.0], "平台": [0.8, 0.6], "甲方": [0.0, 1.0]}
    from arrow_lake.knowledge_graph.orphan_linker import link_orphans

    _, new_rels = link_orphans(
        result, ec, _emb(emb),
        template_path="entity_graph", threshold=0.7, max_partners=3, max_links=10,
    )
    assert new_rels == []


def test_orphan_without_embedding_skipped() -> None:
    # orphan missing from embeddings dict → skipped (no crash, no link).
    result = _result(
        [_e("甲方", "主体"), _e("平台", "软件"), _e("响应时间", "指标")],
        [_rel("甲方", "平台", "提供")],
    )
    ec = {"甲方": ["c1"], "平台": ["c1"], "响应时间": ["c1"]}
    emb = {"平台": [0.8, 0.6], "甲方": [0.0, 1.0]}  # no 响应时间 vector
    _, new_rels = _link(result, ec, emb)
    assert new_rels == []


# ---------------------------------------------------------------------------
# Caps + idempotency
# ---------------------------------------------------------------------------


def test_max_partners_cap() -> None:
    # orphan 软件 co-occurs with 5 connected 软件; max_partners=2 → 2 links.
    ents = [_e("S0", "软件")] + [_e(f"S{i}", "软件") for i in range(1, 6)]
    # connect S1..S5 among themselves
    rels = [_rel("S1", "S2", "集成"), _rel("S3", "S4", "集成"), _rel("S5", "S1", "集成")]
    result = _result(ents, rels)
    ec = {f"S{i}": ["c1"] for i in range(6)}  # all co-occur in c1
    emb = {f"S{i}": [1.0, 0.0] for i in range(6)}  # identical → sim 1.0
    _, new_rels = _link(result, ec, emb, max_partners=2)
    assert len(new_rels) == 2
    # every link endpoint-includes the orphan S0
    assert all(r.source == "S0" or r.target == "S0" for r in new_rels)


def test_max_links_cap() -> None:
    # 3 orphans each co-occur with a connected hub; max_links=2 → 2 total.
    ents = [_e("H", "软件"), _e("G", "软件"),
            _e("O1", "指标"), _e("O2", "指标"), _e("O3", "指标")]
    rels = [_rel("H", "G", "集成")]  # H/G connected; O1/O2/O3 orphans
    result = _result(ents, rels)
    ec = {"H": ["c1"], "G": ["c1"], "O1": ["c1"], "O2": ["c1"], "O3": ["c1"]}
    emb = {k: [1.0, 0.0] for k in ["H", "G", "O1", "O2", "O3"]}
    _, new_rels = _link(result, ec, emb, max_links=2, max_partners=5)
    assert len(new_rels) == 2


def test_idempotent_second_run_noop() -> None:
    # After the first run, former orphans are now endpoints → connected → no orphans.
    result = _result(
        [_e("甲方", "主体"), _e("平台", "软件"), _e("响应时间", "指标")],
        [_rel("甲方", "平台", "提供")],
    )
    ec = {"甲方": ["c1"], "平台": ["c1"], "响应时间": ["c1"]}
    emb = {"响应时间": [1.0, 0.0], "平台": [0.8, 0.6], "甲方": [0.0, 1.0]}
    new_result, new_rels = _link(result, ec, emb)
    assert len(new_rels) == 1
    # second pass over the spliced result
    _, new_rels2 = _link(new_result, ec, emb)
    assert new_rels2 == []
