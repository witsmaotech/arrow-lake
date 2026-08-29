"""F4.3 — 四策略采样引擎(v1.11.3 MS4 W2.1)。

旁路纯函数:行/分数/向量/死信 ID 全部入参,**零 Lake 依赖**——数据读取
(源表/死信表/FAISS)在 dispatch 层组装,便于全单测。红线:采样只读
已有数据集,不挂任何 ingest 钩子(设计 §0)。

* uncertainty  — quality_score 升序(置信最低优先;主动学习)
* diversity    — k-center greedy(embedding 空间最远点;覆盖分布)
* failure_case — 死信表序(质量门拒的困难样本;时间序即列表序)
* committee    — 多模板分歧序(调用方算好,列表序即分歧大到小)

预算(S3):默认 40/30/20/10;权重不求和为 1(按比例归一);某策略数据
源缺失 → 剔除后重归一;配额用最大余数法(总名额守恒);策略池耗尽不
强补(短给)。全部源缺失 → 顺序退化(sequential)。跨策略去重:策略按
权重序先到先得,``SampledRow.strategy`` 记首个选中它的策略。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = ["SampleBudget", "SampledRow", "sample_rows"]

_STRATEGY_ORDER = ("uncertainty", "diversity", "failure_case", "committee")


@dataclass(frozen=True)
class SampleBudget:
    """策略相对占比(默认 40/30/20/10,S3);按比例归一,不要求和为 1。"""

    uncertainty: float = 0.4
    diversity: float = 0.3
    failure_case: float = 0.2
    committee: float = 0.1


@dataclass(frozen=True)
class SampledRow:
    """一个被采中的行:源行标识 + 命中它的首个策略。"""

    row_id: str
    strategy: str


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.dist(a, b)


def _kcenter(ids: list[str], vectors: Mapping[str, Sequence[float]], n: int) -> list[str]:
    """k-center greedy:从 ids[0] 起,每步选离已选集最远的点。"""
    if n <= 0 or not ids:
        return []
    selected = [ids[0]]
    min_d = {i: _dist(vectors[i], vectors[ids[0]]) for i in ids}
    taken = {ids[0]}
    while len(selected) < min(n, len(ids)):
        best = max((i for i in ids if i not in taken), key=lambda i: min_d[i])
        selected.append(best)
        taken.add(best)
        for i in ids:
            if i not in taken:
                min_d[i] = min(min_d[i], _dist(vectors[i], vectors[best]))
    return selected


def _quotas(total: int, weights: Mapping[str, float]) -> dict[str, int]:
    """最大余数法:名额按权重比例分,总和恰为 total(floor+余数补齐)。"""
    active = {k: w for k, w in weights.items() if w > 0}
    if not active or total <= 0:
        return {k: 0 for k in weights}
    scale = sum(active.values())
    raw = {k: total * w / scale for k, w in active.items()}
    quotas = {k: math.floor(v) for k, v in raw.items()}
    remainder = total - sum(quotas.values())
    by_frac = sorted(active, key=lambda k: raw[k] - math.floor(raw[k]), reverse=True)
    for k in by_frac[:remainder]:
        quotas[k] += 1
    return quotas


def sample_rows(
    *,
    total: int,
    row_ids: Sequence[str],
    quality_scores: Mapping[str, float] | None = None,
    embeddings: Mapping[str, Sequence[float]] | None = None,
    dead_row_ids: Sequence[str] | None = None,
    committee_disagreements: Sequence[str] | None = None,
    budget: SampleBudget = SampleBudget(),
) -> list[SampledRow]:
    """从候选全集按四策略+预算采样(纯函数;详见模块 docstring)。"""
    all_ids = list(dict.fromkeys(row_ids))
    if total <= 0 or not all_ids:
        return []

    pools: dict[str, list[str]] = {}
    if quality_scores:
        scored = [r for r in all_ids if r in quality_scores]
        if scored:
            pools["uncertainty"] = sorted(scored, key=lambda r: quality_scores[r])
    if embeddings:
        embedded = [r for r in all_ids if r in embeddings]
        if embedded:
            pools["diversity"] = embedded
    if dead_row_ids:
        dead_set = set(dead_row_ids)
        dead = [r for r in dict.fromkeys(dead_row_ids) if r in dead_set and r in set(all_ids)]
        if dead:
            pools["failure_case"] = dead
    if committee_disagreements:
        id_set = set(all_ids)
        disagree = [r for r in dict.fromkeys(committee_disagreements) if r in id_set]
        if disagree:
            pools["committee"] = disagree

    if not pools:
        return [SampledRow(r, "sequential") for r in all_ids[:total]]

    weights = {
        "uncertainty": budget.uncertainty,
        "diversity": budget.diversity,
        "failure_case": budget.failure_case,
        "committee": budget.committee,
    }
    usable = {k: weights[k] for k in _STRATEGY_ORDER if k in pools}
    target = min(total, len(all_ids))
    quotas = _quotas(target, usable)

    picked: list[SampledRow] = []
    taken: set[str] = set()
    for strategy in _STRATEGY_ORDER:
        quota = quotas.get(strategy, 0)
        if quota <= 0 or strategy not in pools:
            continue
        if strategy == "diversity":
            available = [r for r in pools[strategy] if r not in taken]
            chosen = _kcenter(available, embeddings or {}, quota)
        else:
            chosen = [r for r in pools[strategy] if r not in taken][:quota]
        for row_id in chosen:
            picked.append(SampledRow(row_id, strategy))
            taken.add(row_id)

    # 策略池耗尽仍有余量 → 按候选序补齐(标注任务凑满 total 是目的,
    # 策略只决定"先标哪些";无策略命中 = sequential)。
    if len(picked) < target:
        for row_id in all_ids:
            if len(picked) >= target:
                break
            if row_id not in taken:
                picked.append(SampledRow(row_id, "sequential"))
                taken.add(row_id)
    return picked
