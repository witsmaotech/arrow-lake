"""F5.1 — 五维加权评分 / 星级 / 准入门 / 一票否决(v1.11.4 MS5 W1.4)。

⚠️ 命名注记:``quality/scoring.py`` 是 Story 4.13 的**行级**质量分
(ingest 入口门消费,零触碰);本模块是**数据集级**五维报告评分——
发布门侧,旁路只读。

评分口径(设计 §2.2,手册第五章):
* **加权总分** = Σ(维度分×权重) / Σ(assessed 维度权重)——未评估
  维度(score=None)剔除后**重归一**,不造假分(降级在报告另标);
* **星级**:<60 ★ / 60-74 ★★ / 75-84 ★★★ / 85-94 ★★★★ / ≥95
  ★★★★★(无 assessed 维度 → 0 星);
* **准入门**:85=bronze(可发布)/ 95=silver(推荐)/ 98=gold(标杆),
  发布层(W3)经 ``evaluate_admission`` 消费,含**拒绝劣化**(总分低于
  已发布版本 → 拒;force override 是路由层职责);
* **一票否决**:只在维度**已评估**时判定(降级 ≠ 否决——W1 relevance
  未接线/无标注数据集不得被拦死);``unmasked_corpus_publish`` 是语料
  导出期检查,assess 期恒不触发。

纯函数旁路:输入 DimensionResult 集 + 生效配置,零 Lake 依赖。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from arrow_lake.quality.dimensions import DimensionResult
from arrow_lake.quality.spec import ResolvedQualitySpec

__all__ = [
    "AdmissionDecision",
    "ScoredReport",
    "evaluate_admission",
    "score_dimensions",
    "star_for",
]

#: 必填列缺失率否决线(设计 §2.2 一票否决:>5%)。
REQUIRED_MISSING_VETO = 0.05


@dataclass(frozen=True)
class ScoredReport:
    """一次评估的评分结论(报告持久化主体)。"""

    dimensions: dict[str, DimensionResult]
    total_score: float | None        # 重归一加权总分;None=无 assessed 维度
    star: int                        # 0-5(0=不可评)
    admission: str                   # gold | silver | bronze | none
    verdict: str                     # pass | degraded | veto
    vetoes: tuple[dict[str, Any], ...]
    degraded: tuple[str, ...]        # 未评估维度名


@dataclass(frozen=True)
class AdmissionDecision:
    """发布准入判定(W3 release 端点消费)。"""

    tier: str                        # gold | silver | bronze | none
    allowed: bool
    reasons: tuple[str, ...]


def star_for(total: float) -> int:
    """星级档(边界含下限:60→★★,75→★★★,85→★★★★,95→★★★★★)。"""
    if total < 60:
        return 1
    if total < 75:
        return 2
    if total < 85:
        return 3
    if total < 95:
        return 4
    return 5


def _tier_for(total: float, admission: tuple[float, float, float]) -> str:
    bronze, silver, gold = admission
    if total >= gold:
        return "gold"
    if total >= silver:
        return "silver"
    if total >= bronze:
        return "bronze"
    return "none"


def _evaluate_vetoes(
    dimensions: Mapping[str, DimensionResult], spec: ResolvedQualitySpec,
) -> tuple[dict[str, Any], ...]:
    """逐否决项判定;维度未评估 → 不触发(降级语义)。"""
    out: list[dict[str, Any]] = []
    for kind in spec.vetoes:
        if kind == "accuracy_below_threshold":
            r = dimensions.get("accuracy")
            threshold = spec.thresholds.get("accuracy")
            if (r is not None and r.score is not None and threshold is not None
                    and r.score < threshold):
                out.append({
                    "kind": kind, "dimension": "accuracy",
                    "score": r.score, "threshold": threshold,
                })
        elif kind == "relevance_below_threshold":
            r = dimensions.get("relevance")
            threshold = spec.thresholds.get("relevance")
            if (r is not None and r.score is not None and threshold is not None
                    and r.score < threshold):
                out.append({
                    "kind": kind, "dimension": "relevance",
                    "score": r.score, "threshold": threshold,
                })
        elif kind == "required_missing_gt_5pct":
            comp = dimensions.get("completeness")
            if comp is None:
                continue
            for check in comp.details.get("checks", []):
                if (check.get("kind") == "required"
                        and check.get("missing_rate", 0.0) > REQUIRED_MISSING_VETO):
                    out.append({
                        "kind": kind, "column": check.get("column"),
                        "missing_rate": check.get("missing_rate"),
                        "threshold": REQUIRED_MISSING_VETO,
                    })
                    break
        # unmasked_corpus_publish:语料导出期检查,assess 期不触发
    return tuple(out)


def score_dimensions(
    dimensions: Mapping[str, DimensionResult], spec: ResolvedQualitySpec,
) -> ScoredReport:
    """维度结果集 + 生效配置 → 加权总分/星级/准入/否决/降级清单。"""
    dims = dict(dimensions)
    assessed = {n: r for n, r in dims.items() if r.score is not None}
    # 降级 = 五维全集(权重键)中未评估的维度——显式 None 或整槽缺席同义
    degraded = tuple(sorted(n for n in spec.weights if n not in assessed))

    weight_sum = sum(spec.weights.get(n, 0.0) for n in assessed)
    total: float | None = None
    if assessed and weight_sum > 0:
        weighted = sum(
            r.score * spec.weights.get(n, 0.0) for n, r in assessed.items()
        )
        total = round(weighted / weight_sum, 4)

    vetoes = _evaluate_vetoes(dims, spec)
    verdict = "veto" if vetoes else ("degraded" if degraded else "pass")
    return ScoredReport(
        dimensions=dims,
        total_score=total,
        star=star_for(total) if total is not None else 0,
        admission=_tier_for(total, spec.admission) if total is not None else "none",
        verdict=verdict,
        vetoes=vetoes,
        degraded=degraded,
    )


def evaluate_admission(
    report: ScoredReport, *, previous_total: float | None,
) -> AdmissionDecision:
    """发布准入判定:无否决 + ≥bronze + 不劣化(拒绝劣化发布,S8)。"""
    reasons: list[str] = [f"veto:{v['kind']}" for v in report.vetoes]
    if report.total_score is None:
        return AdmissionDecision(
            tier="none", allowed=False,
            reasons=tuple([*reasons, "no_assessed_dimensions"]),
        )
    if report.admission == "none":
        reasons.append("below_bronze")
    if previous_total is not None and report.total_score < previous_total:
        reasons.append("regression")
    return AdmissionDecision(
        tier=report.admission, allowed=not reasons, reasons=tuple(reasons),
    )
