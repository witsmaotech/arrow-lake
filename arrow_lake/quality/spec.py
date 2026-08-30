"""F5.1 — 五维门生效配置(v1.11.4 MS5 W1.1)。

契约 ``quality:`` 节(``QualitySpec``,登记不校验)与**业务手册默认
常量**合成为生效配置(``ResolvedQualitySpec``):评估/评分/发布层全
消费这一份,契约不写即默认,零配置可用。

合成规则(设计 §2.2/§2.3):
* ``weights`` 写了 → **整体替换**五维向量(未列维度=0)→ 按和归一
  (权重和恒 1);没写 → 手册默认 .20/.35/.20/.15/.10;
* ``thresholds`` 逐键覆盖默认;``critical=true`` 把准确性门槛抬到 95
  (与显式值取 max——关键集语义只紧不松);
* ``veto`` 写了 → 整体替换默认否决集;``admission``/``timeliness``
  写了即覆盖,缺省 85/95/98 与 72h。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from arrow_lake.contract.schema import (
    KNOWN_QUALITY_VETOES,
    QualitySpec,
)
from arrow_lake.quality.drift import DEFAULT_DRIFT_KL

__all__ = [
    "CRITICAL_ACCURACY_THRESHOLD",
    "DEFAULT_ADMISSION",
    "DEFAULT_DRIFT_KL",
    "DEFAULT_MAX_P95_HOURS",
    "DEFAULT_THRESHOLDS",
    "DEFAULT_VETOES",
    "DEFAULT_WEIGHTS",
    "ResolvedQualitySpec",
    "resolve_quality_spec",
]

#: 手册口径默认(设计 §2.1;权重和恒 1)。
DEFAULT_WEIGHTS: Mapping[str, float] = MappingProxyType({
    "relevance": 0.20, "accuracy": 0.35, "completeness": 0.20,
    "diversity": 0.15, "timeliness": 0.10,
})
#: 维度门槛(分):κ×100 ≥ 81(关键集 95)、相关性 ≥ 90。
DEFAULT_THRESHOLDS: Mapping[str, float] = MappingProxyType({
    "accuracy": 81.0, "relevance": 90.0,
})
#: 一票否决默认集(评估期只可能触发前三;unmasked_corpus_publish 在
#: 发布语料时检查,assess 报告中恒 not-triggered)。
DEFAULT_VETOES: tuple[str, ...] = (
    "accuracy_below_threshold",
    "relevance_below_threshold",
    "required_missing_gt_5pct",
    "unmasked_corpus_publish",
)
assert set(DEFAULT_VETOES) == set(KNOWN_QUALITY_VETOES)  # 注册表对齐
#: 准入门(发布层消费):85=可发布(铜)/ 95=推荐(银)/ 98=标杆(金)。
DEFAULT_ADMISSION: tuple[float, float, float] = (85.0, 95.0, 98.0)
#: 标注延迟 p95 上限(小时,S5 领域参数)。
DEFAULT_MAX_P95_HOURS: float = 72.0
#: critical 数据集的准确性门槛(设计 §2.1:关键集 ≥95)。
CRITICAL_ACCURACY_THRESHOLD: float = 95.0


class ResolvedQualitySpec:
    """合成后的生效配置(只读;weights 已归一,和恒 1)。"""

    __slots__ = (
        "_weights",
        "admission",
        "critical",
        "drift_kl",
        "max_p95_hours",
        "thresholds",
        "vetoes",
    )

    def __init__(
        self,
        *,
        weights: Mapping[str, float],
        thresholds: Mapping[str, float],
        vetoes: tuple[str, ...],
        admission: tuple[float, float, float],
        max_p95_hours: float,
        critical: bool,
        drift_kl: float,
    ) -> None:
        object.__setattr__(self, "_weights", MappingProxyType(dict(weights)))
        object.__setattr__(self, "thresholds", MappingProxyType(dict(thresholds)))
        object.__setattr__(self, "vetoes", vetoes)
        object.__setattr__(self, "admission", admission)
        object.__setattr__(self, "max_p95_hours", max_p95_hours)
        object.__setattr__(self, "critical", critical)
        object.__setattr__(self, "drift_kl", drift_kl)

    @property
    def weights(self) -> Mapping[str, float]:
        return self._weights  # type: ignore[attr-defined,no-any-return]

    def __repr__(self) -> str:  # pragma: no cover — 调试便利
        return (
            f"ResolvedQualitySpec(weights={dict(self._weights)}, "  # type: ignore[attr-defined]
            f"thresholds={dict(self.thresholds)}, vetoes={self.vetoes}, "
            f"admission={self.admission}, max_p95_hours={self.max_p95_hours}, "
            f"critical={self.critical})"
        )


def resolve_quality_spec(q: QualitySpec | None) -> ResolvedQualitySpec:
    """契约 quality 节 + 默认常量 → 生效配置(见模块 docstring 合成规则)。"""
    if q is None:
        return ResolvedQualitySpec(
            weights=DEFAULT_WEIGHTS,
            thresholds=DEFAULT_THRESHOLDS,
            vetoes=DEFAULT_VETOES,
            admission=DEFAULT_ADMISSION,
            max_p95_hours=DEFAULT_MAX_P95_HOURS,
            critical=False,
            drift_kl=DEFAULT_DRIFT_KL,
        )

    if q.weights is None:
        weights: dict[str, float] = dict(DEFAULT_WEIGHTS)
    else:
        raw = dict(q.weights)
        total = sum(raw.values())
        weights = {k: v / total for k, v in raw.items()}

    thresholds = dict(DEFAULT_THRESHOLDS)
    if q.thresholds is not None:
        thresholds.update(q.thresholds)
    if q.critical:
        thresholds["accuracy"] = max(
            thresholds["accuracy"], CRITICAL_ACCURACY_THRESHOLD,
        )

    return ResolvedQualitySpec(
        weights=weights,
        thresholds=thresholds,
        vetoes=q.veto if q.veto is not None else DEFAULT_VETOES,
        admission=(
            (q.admission.bronze, q.admission.silver, q.admission.gold)
            if q.admission is not None else DEFAULT_ADMISSION
        ),
        max_p95_hours=(
            q.timeliness.max_p95_hours
            if q.timeliness is not None else DEFAULT_MAX_P95_HOURS
        ),
        critical=q.critical,
        drift_kl=q.drift_kl if q.drift_kl is not None else DEFAULT_DRIFT_KL,
    )
