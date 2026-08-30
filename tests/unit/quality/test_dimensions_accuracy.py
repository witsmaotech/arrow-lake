"""W1.3 — 准确性:ADL 全量聚合 κ(v1.11.4 MS5)。

修 MS4 W5 已知限制(project_kappa 是轮级):本模块从 **ADL 全量**
(``{ds}_adl``,append-only SoT)按 ``(source_row_id, annotator_id)`` 取
**最新 adl_version** 聚合重算 Fleiss' κ —— 项目全生命周期口径。

signature 重建:ADL span 不落 text,但同行的 ``text = 行文本[start:end]``
是 (start,end) 的函数——对**同一行**的标注比较,(label,start,end) 与
(label,start,end,text) 等价(见 dimensions.compute_accuracy docstring)。

降级:空 ADL / 无双标注行 → score=None(报告标数据源,评分层跳过)。
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from arrow_lake.annotation.adl import ADL_SCHEMA
from arrow_lake.quality.dimensions import compute_accuracy


def _ann(
    row: str, annotator: str, version: int = 1, *,
    scenario: str = "s1", objects: list[dict] | None = None,
    rules: list[str] | None = None, relations: list[dict] | None = None,
) -> dict:
    return {
        "adl_id": f"{row}-{annotator}-v{version}",
        "source_dataset": "alerts",
        "source_row_id": row,
        "objects": objects or [],
        "events": [],
        "rules_applied": rules or [],
        "scenario": scenario,
        "relations": relations or [],
        "annotator_id": annotator,
        "annotated_at": "2026-08-30T00:00:00+00:00",
        "review_status": "approved",
        "reviewer_id": "",
        "batch_id": "b1",
        "adl_version": version,
    }


def _adl(rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=ADL_SCHEMA)


SPAN_A = [{"label": "阀门", "start": 0, "end": 2}]
SPAN_B = [{"label": "管道", "start": 5, "end": 7}]


def test_perfect_agreement_kappa_one() -> None:
    rows = [
        _ann("r1", "ann1", objects=SPAN_A), _ann("r1", "ann2", objects=SPAN_A),
        _ann("r2", "ann1", objects=SPAN_B), _ann("r2", "ann2", objects=SPAN_B),
    ]
    res = compute_accuracy(_adl(rows))
    assert res.score == 100.0
    assert res.source == "adl"
    assert res.details["kappa"] == pytest.approx(1.0)
    assert res.details["tasks"] == 2 and res.details["n_raters"] == 2


def test_disagreement_negative_kappa_clamped_to_zero() -> None:
    rows = [
        _ann("r1", "ann1", objects=SPAN_A), _ann("r1", "ann2", objects=SPAN_A),
        _ann("r2", "ann1", objects=SPAN_A), _ann("r2", "ann2", objects=SPAN_A),
        _ann("r3", "ann1", objects=SPAN_A), _ann("r3", "ann2", objects=SPAN_B),
        _ann("r4", "ann1", objects=SPAN_B), _ann("r4", "ann2", objects=SPAN_A),
    ]
    res = compute_accuracy(_adl(rows))
    assert res.details["kappa"] == pytest.approx(-1 / 3, abs=1e-3)
    assert res.score == 0.0  # 负 κ 低于随机,clamp 到 0 分(κ 原值在 details)


def test_latest_version_wins_per_row_annotator() -> None:
    # ann1 重标注:v1=A → v2=B;ann2 只有一版 B。最新版口径下两标注者
    # 全一致(κ=1);若按轮级/含旧版口径会被稀释。
    rows = [
        _ann("r1", "ann1", version=1, objects=SPAN_A),
        _ann("r1", "ann1", version=2, objects=SPAN_B),
        _ann("r1", "ann2", objects=SPAN_B),
    ]
    res = compute_accuracy(_adl(rows))
    assert res.details["kappa"] == pytest.approx(1.0)
    assert res.details["adl_rows"] == 3 and res.details["tasks"] == 1


def test_empty_adl_not_assessed() -> None:
    res = compute_accuracy(None)
    assert res.score is None
    assert res.details["note"] == "no annotations"
    empty = compute_accuracy(_adl([]))
    assert empty.score is None


def test_single_annotator_only_not_assessed() -> None:
    rows = [_ann("r1", "ann1"), _ann("r2", "ann1")]
    res = compute_accuracy(_adl(rows))
    assert res.score is None
    assert res.details["note"] == "no double-annotated rows"


def test_mixed_single_and_double_rows() -> None:
    rows = [
        _ann("r1", "ann1", objects=SPAN_A), _ann("r1", "ann2", objects=SPAN_A),
        _ann("r2", "ann1"),  # 单标注行剔除,不入 κ
    ]
    res = compute_accuracy(_adl(rows))
    assert res.score == 100.0
    assert res.details["tasks"] == 1
    assert res.details["excluded_single"] == 1


def test_partial_agreement_positive_kappa() -> None:
    # 平衡边际下部分一致 → 0 < κ < 1。⚠️构造注记:2 标注者的 Fleiss κ
    # 有著名悖论(Felipe-Cicchetti)——不对称边际下哪怕只有一行分歧,
    # κ 也是负的;正 κ 需要 2×(A,A) + 1×(B,B) + 1×(A,B) 这类平衡形态
    # (此构造 κ = 0.4667)。
    rows = [
        _ann("r1", "ann1", objects=SPAN_A), _ann("r1", "ann2", objects=SPAN_A),
        _ann("r2", "ann1", objects=SPAN_A), _ann("r2", "ann2", objects=SPAN_A),
        _ann("r3", "ann1", objects=SPAN_B), _ann("r3", "ann2", objects=SPAN_B),
        _ann("r4", "ann1", objects=SPAN_A), _ann("r4", "ann2", objects=SPAN_B),
    ]
    res = compute_accuracy(_adl(rows))
    assert res.details["kappa"] == pytest.approx(0.4667, abs=1e-3)
    assert 0.0 < res.score < 100.0


def test_accuracy_excludes_relevance_choice_rows() -> None:
    """W4 e2e 实证:同 row 的 Choices-only(relevance)行不得混入 κ 评分者集。"""
    rel = [{**_ann("r0", "ann1"), "adl_id": "r0-ann1-rel",
            "objects": [], "rules_applied": [], "scenario": "高相关"},
           {**_ann("r1", "ann1"), "adl_id": "r1-ann1-rel",
            "objects": [], "rules_applied": [], "scenario": "不相关"}]
    rows = [
        _ann("r0", "ann1", objects=SPAN_A), _ann("r0", "ann2", objects=SPAN_A),
        _ann("r1", "ann1", objects=SPAN_B), _ann("r1", "ann2", objects=SPAN_B),
        *rel,
    ]
    res = compute_accuracy(_adl(rows))
    assert res.details["kappa"] == pytest.approx(1.0)
    assert res.details["relevance_rows_excluded"] == 2
