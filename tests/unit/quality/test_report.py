"""W1.4 — 加权评分+星级+准入门+一票否决(v1.11.4 MS5)。

评分口径(设计 §2.2):
* 加权总分 = Σ(维度分×权重) / Σ(assessed 维度权重)——**未评估维度
  剔除后重归一**(W1 relevance 未接线/无标注数据集不造假分);
* 星级 <60 ★ / 60-74 ★★ / 75-84 ★★★ / 85-94 ★★★★ / ≥95 ★★★★★;
* 准入门 85=bronze / 95=silver / 98=gold(<bronze 不可发布);
* 一票否决:κ 门槛(accuracy 分 < threshold,关键集 95)、相关性
  <90、必填列缺失 >5%——**只在维度已评估时判定**(降级 ≠ 否决);
* 拒绝劣化:发布层比较 previous_total(W3 消费)。
"""

from __future__ import annotations

import pytest
from arrow_lake.quality.dimensions import DimensionResult
from arrow_lake.quality.report import (
    evaluate_admission,
    score_dimensions,
    star_for,
)
from arrow_lake.quality.spec import resolve_quality_spec

SPEC = resolve_quality_spec(None)


def _dims(**scores: float | None) -> dict[str, DimensionResult]:
    out = {}
    for name, score in scores.items():
        out[name] = DimensionResult(name=name, score=score, details={})
    return out


FULL = _dims(relevance=90.0, accuracy=85.0, completeness=100.0,
             diversity=50.0, timeliness=100.0)
# 手册权重:.20/.35/.20/.15/.10 → 18+29.75+20+7.5+10 = 85.25


# --- 加权与降级 --------------------------------------------------------------

def test_weighted_total_all_assessed() -> None:
    rep = score_dimensions(FULL, SPEC)
    assert rep.total_score == pytest.approx(85.25)
    assert rep.star == 4 and rep.admission == "bronze"
    assert rep.degraded == () and rep.verdict == "pass"


def test_unassessed_dimension_renormalizes() -> None:
    dims = _dims(accuracy=100.0, completeness=100.0, diversity=100.0,
                 timeliness=100.0)  # relevance 缺席
    rep = score_dimensions(dims, SPEC)
    assert rep.total_score == pytest.approx(100.0)
    assert rep.degraded == ("relevance",)
    assert rep.verdict == "degraded"


def test_nothing_assessed() -> None:
    rep = score_dimensions(_dims(relevance=None, accuracy=None), SPEC)
    assert rep.total_score is None
    assert rep.star == 0 and rep.admission == "none"


# --- 星级档 ------------------------------------------------------------------

@pytest.mark.parametrize("total,star", [
    (0.0, 1), (59.9, 1), (60.0, 2), (74.9, 2),
    (75.0, 3), (84.9, 3), (85.0, 4), (94.9, 4), (95.0, 5), (100.0, 5),
])
def test_star_bands(total: float, star: int) -> None:
    assert star_for(total) == star


# --- 准入档 ------------------------------------------------------------------

@pytest.mark.parametrize("total,tier", [
    (84.9, "none"), (85.0, "bronze"), (94.9, "bronze"),
    (95.0, "silver"), (97.9, "silver"), (98.0, "gold"), (100.0, "gold"),
])
def test_admission_tiers(total: float, tier: str) -> None:
    # 单维评估:accuracy 权重归一后独占 → 维度分即总分
    rep = score_dimensions(_dims(accuracy=total), SPEC)
    assert rep.total_score == pytest.approx(total)
    assert rep.admission == tier


# --- 一票否决 ----------------------------------------------------------------

def test_veto_accuracy_below_threshold() -> None:
    dims = _dims(accuracy=80.9, completeness=100.0)  # 80.9 < 81
    rep = score_dimensions(dims, SPEC)
    assert [v["kind"] for v in rep.vetoes] == ["accuracy_below_threshold"]
    assert rep.verdict == "veto"


def test_veto_critical_raises_bar() -> None:
    from arrow_lake.contract.schema import QualitySpec
    spec = resolve_quality_spec(QualitySpec(critical=True))
    dims = _dims(accuracy=90.0, completeness=100.0)  # ≥81 但 <95(关键集)
    rep = score_dimensions(dims, spec)
    assert [v["kind"] for v in rep.vetoes] == ["accuracy_below_threshold"]


def test_unassessed_dimension_never_vetoes() -> None:
    dims = _dims(completeness=100.0, diversity=100.0)  # accuracy/relevance 缺席
    rep = score_dimensions(dims, SPEC)
    assert rep.vetoes == ()
    assert rep.verdict == "degraded"


def test_veto_required_missing_gt_5pct() -> None:
    comp = DimensionResult(
        name="completeness", score=50.0,
        details={"checks": [
            {"kind": "required", "column": "severity",
             "missing_rate": 0.06, "passed": False},  # >5% → 否决
            {"kind": "dead_letter", "rate": 0.0, "passed": True},
        ]},
    )
    rep = score_dimensions(
        {"completeness": comp, "diversity": _dims(diversity=100.0)["diversity"]},
        SPEC,
    )
    assert [v["kind"] for v in rep.vetoes] == ["required_missing_gt_5pct"]


def test_veto_relevance_below_threshold() -> None:
    dims = _dims(relevance=89.9, accuracy=100.0, completeness=100.0)
    rep = score_dimensions(dims, SPEC)
    assert "relevance_below_threshold" in [v["kind"] for v in rep.vetoes]


# --- 发布准入判定(W3 消费) ---------------------------------------------------

def test_admission_pass_at_bronze() -> None:
    rep = score_dimensions(FULL, SPEC)  # 85.25 bronze
    decision = evaluate_admission(rep, previous_total=None)
    assert decision.allowed is True and decision.tier == "bronze"
    assert decision.reasons == ()


def test_admission_blocked_below_bronze() -> None:
    dims = _dims(accuracy=70.0, completeness=100.0, diversity=50.0,
                 timeliness=100.0)  # ≈ 79 < 85
    rep = score_dimensions(dims, SPEC)
    decision = evaluate_admission(rep, previous_total=None)
    assert decision.allowed is False
    assert "below_bronze" in decision.reasons


def test_admission_blocked_by_veto() -> None:
    dims = _dims(accuracy=50.0, completeness=100.0, diversity=100.0,
                 timeliness=100.0)  # 高分 completeness 抬总分过 bronze,但 κ 否决
    rep = score_dimensions(dims, SPEC)
    decision = evaluate_admission(rep, previous_total=None)
    assert decision.allowed is False
    assert any(r.startswith("veto:") for r in decision.reasons)


def test_admission_blocked_by_regression() -> None:
    dims = _dims(accuracy=90.0, completeness=100.0, diversity=100.0,
                 timeliness=100.0)  # 重归一后总分 95.625
    rep = score_dimensions(dims, SPEC)
    assert rep.total_score == pytest.approx(95.625)
    decision = evaluate_admission(rep, previous_total=97.5)
    assert decision.allowed is False
    assert "regression" in decision.reasons
    assert evaluate_admission(rep, previous_total=95.0).allowed is True


def test_admission_none_total_blocked() -> None:
    rep = score_dimensions(_dims(relevance=None), SPEC)
    decision = evaluate_admission(rep, previous_total=None)
    assert decision.allowed is False
