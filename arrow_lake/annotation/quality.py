"""F4.6 — 标注质检:Fleiss' kappa + 仲裁状态机(v1.11.3 MS4 W3.3)。

旁路纯函数(零 Lake/LS 依赖)。两件事:

* **任务级仲裁状态机**(`adjudicate`,S6):同任务多标注全同 signature
  且人数 ≥ min_annotators → ``approved``;有分歧 → ``arbitration``
  (L4 三审,专家终审不可上诉);单人且 min>1 → ``pending``(等第二个
  标注);``min_annotators=1`` → 单标注直接 approved(试点单人标注的
  风险表缓解);ground_truth 免检。
* **项目级 kappa**(`project_kappa`,MS5 准确性信号):跨任务全局
  Fleiss' kappa,只在 signature 类别上算——结构化标注(spans+relations)
  的工程简化:signature = canonical 排序串,完全一致的标注才同类。

Fleiss 公式:κ = (P̄ − P̄ₑ)/(1 − P̄ₑ);P̄ᵢ = (Σⱼ nᵢⱼ² − n)/(n(n−1))。
每任务取前 ``n_raters`` 个标注(nᵢⱼ = 类别 j 在任务 i 的票数)。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from arrow_lake.annotation.recover import RecoveredAnnotation

__all__ = [
    "Adjudication",
    "adjudicate",
    "annotation_signature",
    "fleiss_kappa",
    "project_kappa",
]


def annotation_signature(rec: RecoveredAnnotation) -> str:
    """标注 → canonical 类别串(排序拼接;kappa 与一致性都在此类别上)。

    排序保证顺序不敏感(标注者画 region 的先后不影响一致性)。
    """
    parts = [
        "|".join(sorted(f"{s.label}@{s.start}-{s.end}:{s.text}" for s in rec.objects)),
        "|".join(sorted(f"{s.label}@{s.start}-{s.end}:{s.text}" for s in rec.events)),
        "|".join(sorted(f"{t.subject}>{t.predicate}>{t.object}" for t in rec.relations)),
        "|".join(sorted(rec.rules_applied)),
        rec.scenario,
    ]
    return "§".join(parts)


def fleiss_kappa(labels: Sequence[Sequence[str]], *, n_raters: int) -> float | None:
    """经典 Fleiss' kappa;输入不足(任务数 <1 或某任务不满 n_raters)→ None。"""
    if n_raters < 2 or not labels:
        return None
    rows: list[list[str]] = []
    for row in labels:
        if len(row) < n_raters:
            return None
        rows.append(list(row[:n_raters]))
    categories = sorted({label for row in rows for label in row})
    if len(categories) < 2:
        return 1.0  # 单类别 = 完全一致(退化)
    cat_index = {c: i for i, c in enumerate(categories)}
    n_tasks = len(rows)

    p_bar = 0.0
    category_counts = [0] * len(categories)
    total = n_tasks * n_raters
    for row in rows:
        counts = [0] * len(categories)
        for label in row:
            counts[cat_index[label]] += 1
            category_counts[cat_index[label]] += 1
        p_bar += (sum(c * c for c in counts) - n_raters) / (n_raters * (n_raters - 1))
    p_bar /= n_tasks
    p_e = sum((c / total) ** 2 for c in category_counts)
    if p_e >= 1.0:
        return 1.0
    return (p_bar - p_e) / (1.0 - p_e)


@dataclass(frozen=True)
class Adjudication:
    """任务级仲裁结论。"""

    status: str            # approved | arbitration | pending
    kappa: float | None    # 任务内一致性(单类别=1.0;不足=None)
    signatures: tuple[str, ...]


def adjudicate(
    by_task: Mapping[str, Sequence[RecoveredAnnotation]],
    *,
    threshold: float = 0.80,
    min_annotators: int = 2,
) -> dict[str, Adjudication]:
    """逐任务仲裁:全同 signature(任务内 kappa=1.0 ≥ threshold)→ approved。

    ``threshold`` 语义:S6 的 0.80 在任务内体现为"标注者一致";跨标注者
    部分一致的结构化标注没有连续 kappa(见 :func:`annotation_signature`),
    分歧一律 arbitration。
    """
    out: dict[str, Adjudication] = {}
    for key, anns in by_task.items():
        signatures = tuple(annotation_signature(a) for a in anns)
        if any(a.ground_truth for a in anns):
            out[key] = Adjudication("approved", 1.0, signatures)
            continue
        concordant = len(set(signatures)) == 1
        if concordant:
            if len(anns) >= max(1, min_annotators):
                out[key] = Adjudication("approved", 1.0, signatures)
            else:
                out[key] = Adjudication("pending", None, signatures)
        else:
            out[key] = Adjudication("arbitration", 0.0, signatures)
    return out


def project_kappa(
    by_task: Mapping[str, Sequence[RecoveredAnnotation]],
    *,
    n_raters: int | None = None,
) -> float | None:
    """Fleiss' kappa across the given tasks(MS5 信号);不足 2 人 → None。

    ⚠️ 轮级非累计(review C5):输入是本轮回收的 fresh 标注——增量
    watermark 语义下这是"本轮批内一致性";项目全生命周期 kappa 需从
    ADL 聚合(W5 试点后再做口径)。

    Fleiss 经典要求每任务标注者数一致:``n_raters`` 缺省取各任务标注数
    的最小值,只纳入达标任务。
    """
    eligible = [anns for anns in by_task.values() if len(anns) >= 2]
    if not eligible:
        return None
    n = n_raters or min(len(anns) for anns in eligible)
    rows = [
        [annotation_signature(a) for a in anns[:n]]
        for anns in eligible if len(anns) >= n
    ]
    return fleiss_kappa(rows, n_raters=n)
