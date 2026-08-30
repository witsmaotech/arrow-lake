"""W2.1 — 相关性评分:ADL 聚合(v1.11.4 MS5 F5.2)。

relevance 行识别 = scenario ∈ 三分类标签(D2:高相关/间接相关/不相关)
且 spans/relations/rules **全空**(Choices-only 标注;带结构的是 L4 标注,
scenario 撞名也不认)。

score = 100 × (n_high + 0.5 × n_somewhat) / total(设计 §2.1);
多标注者:per-(row,annotator) 取最新版 → per-row majority,**人工票
优先于 LLM 票**(annotator_id 前缀 ``llm:``),平票取低档(门禁保守);
source = annotation(全人)| llm(全 LLM,降级)| mixed。

配套:``compute_accuracy`` 排除 ``llm:`` 标注者——κ 是人-人一致性,
LLM 行不掺入。
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from arrow_lake.annotation.adl import ADL_SCHEMA
from arrow_lake.quality.dimensions import compute_accuracy, compute_relevance

SPAN = [{"label": "阀门", "start": 0, "end": 2}]


def _rel(row: str, label: str, annotator: str = "ann1", version: int = 1) -> dict:
    return {
        "adl_id": f"{row}-{annotator}-{label}-v{version}",
        "source_dataset": "alerts", "source_row_id": row,
        "objects": [], "events": [], "rules_applied": [],
        "scenario": label, "relations": [],
        "annotator_id": annotator, "annotated_at": "2026-08-30T00:00:00+00:00",
        "review_status": "approved", "reviewer_id": "", "batch_id": "b",
        "adl_version": version,
    }


def _l4(row: str, annotator: str = "ann1") -> dict:
    rec = _rel(row, "高相关", annotator)
    rec["adl_id"] = f"{row}-{annotator}-l4"
    rec["objects"] = SPAN  # 结构化标注 → 非 relevance 行
    return rec


def _adl(rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=ADL_SCHEMA)


def test_score_formula() -> None:
    rows = (
        [_rel(f"r{i}", "高相关") for i in range(4)]
        + [_rel(f"r{i}", "间接相关") for i in range(4, 6)]
    )
    res = compute_relevance(_adl(rows))
    # (4 + 0.5×2)/6 = 83.33
    assert res.score == pytest.approx(100 * 5 / 6)
    assert res.source == "annotation"
    assert res.details["counts"] == {"高相关": 4, "间接相关": 2, "不相关": 0}


def test_structured_rows_excluded_even_with_matching_scenario() -> None:
    rows = [_rel("r0", "高相关"), _l4("r1")]  # L4 行 scenario 撞名
    res = compute_relevance(_adl(rows))
    assert res.details["rows"] == 1


def test_latest_version_per_row_annotator() -> None:
    rows = [
        _rel("r0", "不相关", "ann1", version=1),
        _rel("r0", "高相关", "ann1", version=2),  # 重判 → 最新版生效
    ]
    res = compute_relevance(_adl(rows))
    assert res.score == 100.0


def test_empty_or_no_relevance_rows_not_assessed() -> None:
    assert compute_relevance(None).score is None
    assert compute_relevance(_adl([])).score is None
    assert compute_relevance(_adl([_l4("r0")])).score is None


def test_llm_only_degraded_source() -> None:
    rows = [_rel("r0", "高相关", "llm:qwen-turbo"),
            _rel("r1", "不相关", "llm:qwen-turbo")]
    res = compute_relevance(_adl(rows))
    assert res.score == 50.0
    assert res.source == "llm"  # 降级:非人工结论
    assert res.details["assessed_by"] == "llm"


def test_human_preferred_over_llm_per_row() -> None:
    rows = [
        _rel("r0", "高相关", "ann1"),          # 人工票优先
        _rel("r0", "不相关", "llm:qwen"),      # LLM 同行票被压
        _rel("r1", "不相关", "llm:qwen"),      # 该行只有 LLM → 采用
    ]
    res = compute_relevance(_adl(rows))
    assert res.source == "mixed"
    assert res.score == 50.0  # (1 + 0)/2


def test_majority_tie_breaks_low() -> None:
    rows = [
        _rel("r0", "高相关", "ann1"),
        _rel("r0", "不相关", "ann2"),  # 平票 → 低档(不相关)
    ]
    res = compute_relevance(_adl(rows))
    assert res.score == 0.0
    assert res.details["counts"]["不相关"] == 1


def test_accuracy_excludes_llm_annotators() -> None:
    # 人×2 一致 + LLM 行 → κ 只算两位人类
    rows = [
        _l4("r0", "ann1"), _l4("r0", "ann2"),
        _l4("r1", "ann1"), _l4("r1", "ann2"),
        _l4("r0", "llm:qwen"),
    ]
    res = compute_accuracy(_adl(rows))
    assert res.details["kappa"] == pytest.approx(1.0)
    assert res.details["annotators"] == 2  # LLM 未计入


def test_accuracy_llm_only_not_assessed() -> None:
    rows = [_l4("r0", "llm:qwen"), _l4("r1", "llm:qwen")]
    res = compute_accuracy(_adl(rows))
    assert res.score is None  # 排除 LLM 后无双标注行
