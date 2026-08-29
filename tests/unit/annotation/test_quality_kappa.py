"""W3.3 — annotation/quality:Fleiss' kappa + 仲裁状态机(设计 §7 / S6)。

契约:
* ``fleiss_kappa(labels, n_raters)``:经典公式(P̄-P̄e)/(1-P̄e);完全
  一致 = 1.0;完全混乱 ≤ 0;单类别退化 = 1.0;
* ``annotation_signature``:结构化标注 → canonical 串(实体/关系/规则/
  scenario 排序拼接)——kappa 在 signature 类别上算(结构化标注的工程
  简化,S6 阈值语义在整体一致层);
* ``adjudicate``(任务级状态机,S6):全同 signature 且人数达标 →
  approved;分歧 → arbitration;单人且 min_annotators>1 → pending;
  min_annotators=1 → 单标注直接 approved(风险表缓解);
* ``project_kappa``:项目级指标(MS5 信号;<2 人或多类不足 → None)。
"""

from __future__ import annotations

from arrow_lake.annotation.quality import (
    adjudicate,
    annotation_signature,
    fleiss_kappa,
    project_kappa,
)
from arrow_lake.annotation.recover import RecoveredAnnotation, Span


def _ann(annotator: str, *, objs=("硬件",), rels=(), scenario="应急") -> RecoveredAnnotation:
    spans = tuple(Span(t, 0, 1, t) for t in objs)
    return RecoveredAnnotation(
        task_id=1, row_id="r1", strategy="uncertainty",
        annotator_id=annotator, annotated_at="t", ground_truth=False,
        objects=spans, events=(), relations=rels,
        rules_applied=(), scenario=scenario,
    )


class TestFleissKappa:
    def test_perfect_agreement_is_one(self):
        labels = [["A", "A", "A"], ["B", "B", "B"], ["A", "A", "A"]]
        assert fleiss_kappa(labels, n_raters=3) == 1.0

    def test_single_category_degenerate_one(self):
        assert fleiss_kappa([["A", "A"], ["A", "A"]], n_raters=2) == 1.0

    def test_systematic_disagreement_negative(self):
        # 每任务各 raters 一半 A 一半 B(2 raters:n_AA=n_BB=1)
        labels = [["A", "B"], ["B", "A"]] * 5
        assert fleiss_kappa(labels, n_raters=2) < 0.0

    def test_moderate_agreement_between(self):
        labels = [["A", "A", "A"], ["A", "A", "B"], ["B", "B", "B"], ["A", "A", "A"]]
        k = fleiss_kappa(labels, n_raters=3)
        assert 0.0 < k < 1.0

    def test_insufficient_input_raises_or_none(self):
        assert fleiss_kappa([], n_raters=2) is None
        assert fleiss_kappa([["A"]], n_raters=2) is None  # 不满 n_raters


class TestSignature:
    def test_same_content_same_signature(self):
        assert annotation_signature(_ann("7")) == annotation_signature(_ann("8"))

    def test_different_scenario_differs(self):
        assert (
            annotation_signature(_ann("7", scenario="应急"))
            != annotation_signature(_ann("8", scenario="常规"))
        )

    def test_order_insensitive(self):
        a = _ann("7", objs=("硬件", "软件"))
        b = _ann("8", objs=("软件", "硬件"))
        assert annotation_signature(a) == annotation_signature(b)


class TestAdjudicate:
    def test_concordant_two_annotators_approved(self):
        out = adjudicate({("r1",): [_ann("7"), _ann("8")]})
        assert out[("r1",)].status == "approved"
        assert out[("r1",)].kappa == 1.0

    def test_discordant_goes_arbitration(self):
        out = adjudicate(
            {("r1",): [_ann("7", scenario="应急"), _ann("8", scenario="常规")]}
        )
        assert out[("r1",)].status == "arbitration"

    def test_single_annotator_pending_when_min_two(self):
        out = adjudicate({("r1",): [_ann("7")]})
        assert out[("r1",)].status == "pending"

    def test_single_annotator_approved_when_min_one(self):
        out = adjudicate({("r1",): [_ann("7")]}, min_annotators=1)
        assert out[("r1",)].status == "approved"

    def test_majority_concordant_minority_discord(self):
        # 3 人中 2 人同、1 人异 → 任务内不一致 → arbitration
        out = adjudicate(
            {("r1",): [_ann("7"), _ann("8"), _ann("9", scenario="常规")]}
        )
        assert out[("r1",)].status == "arbitration"

    def test_ground_truth_approved_regardless(self):
        gt = RecoveredAnnotation(
            task_id=1, row_id="r1", strategy="", annotator_id="99",
            annotated_at="t", ground_truth=True,
            objects=(Span("硬件", 0, 1, "硬件"),), events=(), relations=(),
            rules_applied=(), scenario="应急",
        )
        out = adjudicate({("r1",): [gt]}, min_annotators=2)
        assert out[("r1",)].status == "approved"  # ground truth 免检(S6)


class TestProjectKappa:
    def test_mixed_project_below_threshold(self):
        """半数任务分歧 + 2 raters → kappa 为负(随机一致性之上无增益)。"""
        by_task = {
            ("r1",): [_ann("7"), _ann("8")],
            ("r2",): [_ann("7", scenario="常规"), _ann("8", scenario="应急")],
        }
        k = project_kappa(by_task)
        assert k is not None and -1.0 < k < 0.80  # 未达 S6 阈值

    def test_all_concordant_one(self):
        by_task = {
            ("r1",): [_ann("7"), _ann("8")],
            ("r2",): [_ann("7"), _ann("8")],
        }
        assert project_kappa(by_task) == 1.0

    def test_all_single_annotator_none(self):
        assert project_kappa({("r1",): [_ann("7")]}) is None
