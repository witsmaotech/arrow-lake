"""W2.1 — annotation/sampler:四策略采样(设计 v1.1 §4 / S3)。

契约:
* 四策略:uncertainty(quality_score 升序)/ diversity(k-center greedy)/
  failure_case(死信序)/ committee(分歧序);
* 预算默认 40/30/20/10,权重可配(S3);某策略数据源缺失 → 配额按剩余
  权重重分配;全部缺失 → 顺序退化(sequential);
* 跨策略去重(先到先得,SampledRow.strategy = 首个选中策略);
* 纯函数:行/分数/向量/死信 ID 全部入参,零 Lake 依赖(读取在 dispatch)。
"""

from __future__ import annotations

import math

import pytest
from arrow_lake.annotation.sampler import SampleBudget, SampledRow, sample_rows

SCORES = {"r1": 0.9, "r2": 0.1, "r3": 0.5, "r4": 0.3, "r5": 0.95}
EMB = {
    "r1": [1.0, 0.0],
    "r2": [1.0, 0.0],   # 与 r1 重合 → 同簇
    "r3": [0.0, 1.0],
    "r4": [0.7, 0.7],
    "r5": [0.9, 0.1],
}


class TestUncertainty:
    def test_picks_lowest_scores_first(self):
        out = sample_rows(total=2, row_ids=list(SCORES), quality_scores=SCORES)
        assert [s.row_id for s in out] == ["r2", "r4"]
        assert all(s.strategy == "uncertainty" for s in out)

    def test_rows_without_score_treated_as_unknown_not_picked(self):
        out = sample_rows(
            total=5, row_ids=["a", "b", "c"],
            quality_scores={"a": 0.8},  # b/c 无分数 → 不进 uncertainty 池
        )
        # a 走 uncertainty;b/c 是补齐(sequential),不是策略命中
        assert [(s.row_id, s.strategy) for s in out] == [
            ("a", "uncertainty"), ("b", "sequential"), ("c", "sequential"),
        ]


class TestDiversity:
    def test_kcenter_spreads_far_points(self):
        out = sample_rows(total=3, row_ids=list(EMB), embeddings=EMB)
        picked = {s.row_id for s in out}
        # r1/r2 同点只会进一个;三个点必含 [1,0] 与 [0,1] 两极
        assert picked & {"r1", "r2"}
        assert "r3" in picked

    def test_diversity_without_embeddings_degrades(self):
        out = sample_rows(total=2, row_ids=["a", "b", "c"])  # 无任何源
        assert [s.strategy for s in out] == ["sequential", "sequential"]


class TestFailureCase:
    def test_dead_rows_selected_in_order(self):
        out = sample_rows(
            total=3, row_ids=["a", "b", "c", "d"],
            dead_row_ids=["d", "b"],
        )
        by_strategy = {s.row_id: s.strategy for s in out}
        assert by_strategy["d"] == "failure_case"
        assert by_strategy["b"] == "failure_case"


class TestCommittee:
    def test_disagreement_rows_selected(self):
        out = sample_rows(
            total=2, row_ids=["a", "b", "c"],
            committee_disagreements=["c"],
        )
        assert {s.row_id: s.strategy for s in out}.get("c") == "committee"


class TestBudget:
    def test_default_weights_40_30_20_10(self):
        ids = [f"r{i}" for i in range(20)]
        # 分数随 i 降序 → uncertainty 取 r16-r19;dead/committee 与之不重叠
        scores = {f"r{i}": 1.0 - i * 0.04 for i in range(20)}
        embs = {f"r{i}": [math.cos(i), math.sin(i)] for i in range(20)}  # 均匀圆
        dead = ids[12:14]           # r12, r13
        disagree = ids[10:11]       # r10
        out = sample_rows(
            total=10, row_ids=ids, quality_scores=scores,
            embeddings=embs, dead_row_ids=dead,
            committee_disagreements=disagree,
        )
        counts: dict[str, int] = {}
        for s in out:
            counts[s.strategy] = counts.get(s.strategy, 0) + 1
        assert counts["uncertainty"] == 4
        assert counts["diversity"] == 3
        assert counts["failure_case"] == 2
        assert counts["committee"] == 1

    def test_missing_source_redistributes_quota(self):
        """diversity 源(embeddings)缺失 → 其 30% 配额按 4:2:1 摊给其余。

        构造:r0-r5 低分(uncertainty 取 r0-r3);r6-r9 高分但属
        dead(r8,r9)/committee(r6,r7)——策略池互不重叠。
        """
        ids = [f"r{i}" for i in range(10)]
        scores = {f"r{i}": (0.9 if i >= 6 else 0.1 + i * 0.05) for i in range(10)}
        out = sample_rows(
            total=7, row_ids=ids, quality_scores=scores,
            dead_row_ids=ids[8:],
            committee_disagreements=ids[6:8],
        )
        counts: dict[str, int] = {}
        for s in out:
            counts[s.strategy] = counts.get(s.strategy, 0) + 1
        # 剩余权重 4:2:1(总 7)→ 4/2/1
        assert counts == {"uncertainty": 4, "failure_case": 2, "committee": 1}

    def test_all_sources_missing_sequential(self):
        out = sample_rows(total=3, row_ids=["a", "b", "c"])
        assert [s.row_id for s in out] == ["a", "b", "c"]

    def test_total_exceeds_candidates_short_gives(self):
        out = sample_rows(total=10, row_ids=["a", "b"], quality_scores={"a": 1.0})
        assert {s.row_id for s in out} == {"a", "b"}

    def test_total_zero_returns_empty(self):
        assert sample_rows(total=0, row_ids=["a"], quality_scores={"a": 1.0}) == []


class TestDedup:
    def test_row_picked_once_keeps_first_strategy(self):
        """r2 既是最低分又被死信 → 只出现一次,strategy=uncertainty(先占)。"""
        out = sample_rows(
            total=4, row_ids=list(SCORES), quality_scores=SCORES,
            dead_row_ids=["r2", "r3"],
        )
        entries = [s.row_id for s in out]
        assert len(entries) == len(set(entries))
        r2 = next(s for s in out if s.row_id == "r2")
        assert r2.strategy == "uncertainty"

    def test_result_type(self):
        out = sample_rows(total=1, row_ids=["a"], quality_scores={"a": 0.5})
        assert isinstance(out[0], SampledRow)


class TestBudgetModel:
    def test_custom_weights(self):
        b = SampleBudget(uncertainty=0.0, diversity=1.0, failure_case=0.0, committee=0.0)
        ids = [f"r{i}" for i in range(4)]
        embs = {r: [float(i), 0.0] for i, r in enumerate(ids)}
        out = sample_rows(total=3, row_ids=ids, embeddings=embs, budget=b)
        assert all(s.strategy == "diversity" for s in out)

    def test_budget_default_matches_design(self):
        b = SampleBudget()
        assert (b.uncertainty, b.diversity, b.failure_case, b.committee) == (0.4, 0.3, 0.2, 0.1)


@pytest.mark.parametrize("bad", [SampleBudget(uncertainty=0.5, diversity=0.3, failure_case=0.2, committee=0.1)])
def test_weights_are_normalized_not_validated_strictly(bad: SampleBudget) -> None:
    """权重不求和为 1 也可用(按比例归一);语义=相对占比。"""
    ids = [f"r{i}" for i in range(10)]
    scores = {r: 0.5 for r in ids}
    out = sample_rows(total=3, row_ids=ids, quality_scores=scores, budget=bad)
    assert len(out) == 3
