"""W1.2 — dimensions.py 完整性/多样性/时效性三维自动评估(v1.11.4 MS5)。

纯函数、旁路只读(热路径零改动):输入 pyarrow 表 + 契约 + 死信计数,
输出 ``DimensionResult``(score 0-100;None=未评估降级)。

* completeness:契约 required 列缺失率(≤1% 过)+ 死信率(≤1% 过)
  = 检查项通过率;
* diversity:类别列(string、基数 ≤50)频率 Gini–Simpson(Σpᵢ²),
  score=(1−max_gini)×100(最差列封顶;单类别退化=0);
* timeliness:新鲜度 + 标注延迟 p95 双指标 SLO 折算(≤max 满分,
  超出按 max/h 比例衰减)。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pytest
from arrow_lake.contract.schema import parse_contract
from arrow_lake.quality.dimensions import (
    compute_diversity,
    compute_timeliness,
    detect_timestamp_column,
)

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _table(rows: list[dict], schema: pa.Schema | None = None) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=schema)


def _contract(yaml_text: str):
    return parse_contract(yaml_text)


REQ = """
dataset: alerts
tables:
  alerts:
    columns:
      - name: severity
        required: true
        enum: [high, low]
      - name: note
        required: false
"""


# === completeness ============================================================

def test_completeness_all_pass() -> None:
    from arrow_lake.quality.dimensions import compute_completeness
    table = _table([{"severity": "high"}, {"severity": "low"}] * 5)
    res = compute_completeness(table, _contract(REQ), dead_letter_rows=0)
    assert res.score == 100.0
    kinds = [c["kind"] for c in res.details["checks"]]
    assert kinds.count("required") == 1 and "dead_letter" in kinds


def test_completeness_required_nulls_fail() -> None:
    from arrow_lake.quality.dimensions import compute_completeness
    rows = [{"severity": "high"}] * 8 + [{"severity": None}] * 2  # 20% 缺失
    res = compute_completeness(_table(rows), _contract(REQ), dead_letter_rows=0)
    sev = next(c for c in res.details["checks"] if c["column"] == "severity")
    assert sev["missing_rate"] == pytest.approx(0.2)
    assert sev["passed"] is False
    # 2 检查项过 1(required 失败,dead letter 过)→ 50 分
    assert res.score == pytest.approx(50.0)


def test_completeness_column_absent_from_schema() -> None:
    from arrow_lake.quality.dimensions import compute_completeness
    table = _table([{"other": 1}])
    res = compute_completeness(table, _contract(REQ), dead_letter_rows=0)
    sev = next(c for c in res.details["checks"] if c["column"] == "severity")
    assert sev["missing_rate"] == 1.0 and sev["passed"] is False


def test_completeness_dead_letter_rate_fails() -> None:
    from arrow_lake.quality.dimensions import compute_completeness
    table = _table([{"severity": "high"}] * 99)
    # 1/100 = 1% 恰在 ≤ 阈值内 → pass 边界
    res = compute_completeness(table, _contract(REQ), dead_letter_rows=1)
    dead = next(c for c in res.details["checks"] if c["kind"] == "dead_letter")
    assert dead["rate"] == pytest.approx(0.01) and dead["passed"] is True
    # 2 条死信 → ~2% > 1% → fail,2 检查项过 1 → 50 分
    res2 = compute_completeness(table, _contract(REQ), dead_letter_rows=2)
    dead2 = next(c for c in res2.details["checks"] if c["kind"] == "dead_letter")
    assert dead2["rate"] > 0.01 and dead2["passed"] is False
    assert res2.score == pytest.approx(50.0)


def test_completeness_boundary_one_pct_passes() -> None:
    from arrow_lake.quality.dimensions import compute_completeness
    rows = [{"severity": "high"}] * 99 + [{"severity": None}]  # 恰 1%
    res = compute_completeness(_table(rows), _contract(REQ), dead_letter_rows=0)
    sev = next(c for c in res.details["checks"] if c["column"] == "severity")
    assert sev["missing_rate"] == pytest.approx(0.01)
    assert sev["passed"] is True  # ≤ 1% 过线


def test_completeness_no_contract() -> None:
    from arrow_lake.quality.dimensions import compute_completeness
    res = compute_completeness(_table([{"x": 1}]), None, dead_letter_rows=0)
    assert res.score == 100.0
    assert res.details["contract"] is False
    assert [c["kind"] for c in res.details["checks"]] == ["dead_letter"]


def test_completeness_empty_table_all_dead() -> None:
    from arrow_lake.quality.dimensions import compute_completeness
    table = _table([], schema=pa.schema([("severity", pa.string())]))
    res = compute_completeness(table, _contract(REQ), dead_letter_rows=5)
    dead = next(c for c in res.details["checks"] if c["kind"] == "dead_letter")
    assert dead["rate"] == 1.0 and dead["passed"] is False
    assert res.score == 0.0


# === diversity ==============================================================

def test_diversity_uniform_two_categories() -> None:
    table = _table([{"cat": "a"}, {"cat": "b"}] * 5)
    res = compute_diversity(table)
    assert res.score == pytest.approx(50.0)  # 1 − (0.25+0.25)
    assert res.details["columns"]["cat"]["gini"] == pytest.approx(0.5)


def test_diversity_skewed() -> None:
    table = _table([{"cat": "a"}] * 9 + [{"cat": "b"}])
    res = compute_diversity(table)
    assert res.details["columns"]["cat"]["gini"] == pytest.approx(0.82)
    assert res.score == pytest.approx(18.0)


def test_diversity_single_category_degenerates_to_zero() -> None:
    table = _table([{"cat": "a"}] * 10)
    res = compute_diversity(table)
    assert res.score == 0.0


def test_diversity_no_categorical_columns_not_assessed() -> None:
    table = _table([{"v": 1.0}, {"v": 2.0}])
    res = compute_diversity(table)
    assert res.score is None


def test_diversity_worst_column_caps() -> None:
    # good 列均匀 4 类(gini=.25)…bad 列单类别(gini=1)→ 取最差
    rows = [{"good": c, "bad": "x"} for c in "abcd"] * 3
    res = compute_diversity(_table(rows))
    assert res.score == 0.0
    assert res.details["worst_column"] == "bad"


def test_diversity_high_cardinality_string_excluded() -> None:
    rows = [{"id": f"row-{i}", "cat": "a"} for i in range(60)]
    res = compute_diversity(_table(rows))
    assert set(res.details["columns"]) == {"cat"}


def test_diversity_nulls_excluded_from_frequency() -> None:
    rows = [{"cat": "a"}, {"cat": "b"}, {"cat": None}]
    res = compute_diversity(_table(rows))
    assert res.details["columns"]["cat"]["gini"] == pytest.approx(0.5)


# === timeliness =============================================================

def test_timeliness_both_within_slo_full_score() -> None:
    res = compute_timeliness(
        freshness_hours=10.0, annotation_delay_p95_hours=20.0,
        max_p95_hours=72.0,
    )
    assert res.score == 100.0
    assert res.details["components"]["freshness"]["score"] == 100.0


def test_timeliness_freshness_exceeds_proportionally() -> None:
    res = compute_timeliness(
        freshness_hours=144.0, annotation_delay_p95_hours=None,
        max_p95_hours=72.0,
    )
    assert res.score == pytest.approx(50.0)  # 100×72/144


def test_timeliness_delay_exceeds_proportionally() -> None:
    res = compute_timeliness(
        freshness_hours=0.0, annotation_delay_p95_hours=180.0,
        max_p95_hours=72.0,
    )
    assert res.score == pytest.approx(70.0)  # mean(100, 100×72/180=40)


def test_timeliness_no_metrics_not_assessed() -> None:
    res = compute_timeliness(
        freshness_hours=None, annotation_delay_p95_hours=None, max_p95_hours=72.0,
    )
    assert res.score is None


def test_timeliness_freshness_only() -> None:
    res = compute_timeliness(
        freshness_hours=36.0, annotation_delay_p95_hours=None, max_p95_hours=72.0,
    )
    assert res.score == 100.0
    assert "annotation_delay_p95" not in res.details["components"]


def test_timeliness_at_threshold_full_score() -> None:
    res = compute_timeliness(
        freshness_hours=72.0, annotation_delay_p95_hours=72.0, max_p95_hours=72.0,
    )
    assert res.score == 100.0


# === timestamp column detection =============================================

_TS = pa.timestamp("s")


def test_detect_timestamp_prefers_updated_at() -> None:
    table = _table(
        [{"created_at": 1, "updated_at": 2, "payload": "x"}],
        schema=pa.schema([
            ("created_at", _TS), ("updated_at", _TS), ("payload", pa.string()),
        ]),
    )
    assert detect_timestamp_column(table) == "updated_at"


def test_detect_timestamp_suffix_then_first_temporal() -> None:
    table = _table(
        [{"occurred_at": 1, "raw_ts": 2}],
        schema=pa.schema([("occurred_at", _TS), ("raw_ts", _TS)]),
    )
    assert detect_timestamp_column(table) == "occurred_at"
    only_odd = _table([{"raw_ts": 1}], schema=pa.schema([("raw_ts", _TS)]))
    assert detect_timestamp_column(only_odd) == "raw_ts"


def test_detect_timestamp_none_when_no_temporal() -> None:
    table = _table([{"time": "not-a-timestamp"}])  # string 名对但类型不对
    assert detect_timestamp_column(table) is None


def test_freshness_hours_from_table() -> None:
    from arrow_lake.quality.dimensions import freshness_hours
    table = _table(
        [{"updated_at": 1}, {"updated_at": 2}],
        schema=pa.schema([("updated_at", _TS)]),
    )
    latest = datetime.fromtimestamp(2, tz=UTC)
    hours = (NOW - latest).total_seconds() / 3600
    assert freshness_hours(table, now=NOW) == pytest.approx(hours)


# === annotation delay join(orchestrator 用) ================================

def _delay_setup():
    from arrow_lake.annotation.adl import ADL_SCHEMA
    src = _table(
        [
            {"text": "alpha alert", "updated_at": 1},   # row0:h(sha1 alpha)
            {"text": "beta leak", "updated_at": 2},
            {"text": "gamma", "updated_at": 3},
        ],
        schema=pa.schema([("text", pa.string()), ("updated_at", _TS)]),
    )
    from arrow_lake.annotation.dispatch import stable_row_id
    r0 = stable_row_id("alpha alert", 0)
    r1 = stable_row_id("beta leak", 1)
    adl_rows = []
    # r0 标注于行时间 +10h;r1 标注于 +100h(把 p95 拖过线)
    for rid, row_ts, lag in ((r0, 1, 10), (r1, 2, 100)):
        adl_rows.append({
            "adl_id": f"{rid}-a1", "source_dataset": "alerts", "source_row_id": rid,
            "objects": [], "events": [], "rules_applied": [], "scenario": "s",
            "relations": [], "annotator_id": "ann1",
            "annotated_at": datetime.fromtimestamp(row_ts + lag * 3600, tz=UTC).isoformat(),
            "review_status": "approved", "reviewer_id": "", "batch_id": "b",
            "adl_version": 1,
        })
    return src, pa.Table.from_pylist(adl_rows, schema=ADL_SCHEMA)


def test_annotation_delay_p95_join() -> None:
    from arrow_lake.quality.dimensions import annotation_delay_p95_hours
    src, adl = _delay_setup()
    p95 = annotation_delay_p95_hours(src, adl, text_column="text")
    assert p95 == pytest.approx(100.0)  # 2 条延迟 {10,100} → p95=100


def test_annotation_delay_no_pairs_or_no_ts() -> None:
    from arrow_lake.quality.dimensions import annotation_delay_p95_hours
    src, adl = _delay_setup()
    # 无时间列 → None
    src_no_ts = _table([{"text": "alpha alert"}])
    assert annotation_delay_p95_hours(src_no_ts, adl, text_column="text") is None
    # ADL 空 → None
    from arrow_lake.annotation.adl import ADL_SCHEMA
    empty = pa.Table.from_pylist([], schema=ADL_SCHEMA)
    assert annotation_delay_p95_hours(src, empty, text_column="text") is None
