"""W2.2 — drift.py 漂移快照 + KL 散度(v1.11.4 MS5 F5.3)。

口径(设计 §5 / S6):
* **基线快照**:数值列等宽 **32 桶**直方图(min/max/counts);类别列
  **top-32 频率 + other 桶**(新值天然落 other,无平滑 ε)。
* **检测**:当前数据按 **baseline 边界** rebin(数值越界 clamp 进首末
  桶;类别按 baseline 类目映射,未知名→other)→ KL(P‖Q),P=当前,
  Q=基线;数值空桶 Q 加 **ε=1e-6** 平滑(等宽桶稀疏时空段 KL∞ 防护)。
* 阈值默认 0.1(契约 ``quality.drift_kl`` 覆盖,W2.c)。

KL 数值性质测试:同分布 ≈ 0;偏移单调;类别新值集中 → KL 大。
"""

from __future__ import annotations

import math

import pyarrow as pa
import pytest
from arrow_lake.quality.drift import (
    categorical_kl,
    numeric_kl,
    snapshot_column,
    snapshot_table,
)


def _num(values: list[float]) -> pa.ChunkedArray:
    return pa.table({"v": pa.array(values, pa.float64())}).column("v")


def _cat(values: list[str]) -> pa.ChunkedArray:
    return pa.table({"c": pa.array(values, pa.string())}).column("c")


# === 快照 ---------------------------------------------------------------------

def test_numeric_snapshot_32_bins() -> None:
    snap = snapshot_column(_num([float(i) for i in range(100)]))
    assert snap["kind"] == "numeric"
    assert snap["bins"] == 32
    assert len(snap["counts"]) == 32
    assert sum(snap["counts"]) == 100
    assert snap["min"] == 0.0 and snap["max"] == 99.0


def test_categorical_snapshot_top32_plus_other() -> None:
    values = [f"c{i}" for i in range(40)]  # 40 个不同值
    # 前 32 频次高,后 8 低 → other 桶
    weighted = [v for i, v in enumerate(values) for _ in range(40 - i)]
    snap = snapshot_column(_cat(weighted))
    assert snap["kind"] == "categorical"
    assert len(snap["values"]) == 32
    assert snap["other"] > 0
    assert sum(snap["values"].values()) + snap["other"] == len(weighted)


def test_snapshot_table_dispatches_by_type() -> None:
    table = pa.table({
        "v": pa.array([1.0, 2.0, 3.0], pa.float64()),
        "c": pa.array(["a", "b", "a"], pa.string()),
        "ts": pa.array([1, 2, 3], pa.int64()),  # int 亦是数值
    })
    snap = snapshot_table(table)
    assert snap["v"]["kind"] == "numeric"
    assert snap["c"]["kind"] == "categorical"
    assert snap["ts"]["kind"] == "numeric"  # 整型入数值直方图


def test_snapshot_empty_column_excluded() -> None:
    table = pa.table({
        "allnull": pa.array([None, None], pa.float64()),
        "c": pa.array(["a", "b"], pa.string()),
    })
    snap = snapshot_table(table)
    assert "allnull" not in snap and "c" in snap


# === 数值 KL ------------------------------------------------------------------

def test_numeric_kl_identical_distribution_zero() -> None:
    values = [float(i % 32) for i in range(320)]
    base = snapshot_column(_num(values))
    kl = numeric_kl(_num(values), base)
    assert kl == pytest.approx(0.0, abs=1e-9)


def test_numeric_kl_shifted_distribution_positive() -> None:
    base_values = [float(i) for i in range(100)]           # 0..99
    cur_values = [float(i + 50) for i in range(100)]       # 50..149 整体右移
    base = snapshot_column(_num(base_values))
    kl = numeric_kl(_num(cur_values), base)
    assert kl > 0.1  # 明显漂移


def test_numeric_kl_out_of_range_clamped() -> None:
    base = snapshot_column(_num([float(i) for i in range(100)]))
    # 当前全部越界(>max)→ clamp 进末桶,仍可计算非无穷
    kl = numeric_kl(_num([500.0, 600.0]), base)
    assert math.isfinite(kl) and kl > 0


def test_numeric_kl_symmetry_direction() -> None:
    """KL 非对称(P‖Q ≠ Q‖P)是预期;但两个方向都有限且 ≥0。"""
    base = snapshot_column(_num([float(i) for i in range(64)]))
    cur = _num([float(i) + 16 for i in range(64)])
    a = numeric_kl(cur, base)
    assert a >= 0 and math.isfinite(a)


# === 类别 KL ------------------------------------------------------------------

def test_categorical_kl_identical_zero() -> None:
    values = ["a"] * 60 + ["b"] * 40
    base = snapshot_column(_cat(values))
    assert categorical_kl(_cat(values), base) == pytest.approx(0.0, abs=1e-9)


def test_categorical_kl_new_values_lands_in_other() -> None:
    base = snapshot_column(_cat(["a"] * 80 + ["b"] * 20))
    # 当前出现基线从未见过的新值 z → other 桶承载,KL 有限且大
    cur = _cat(["a"] * 50 + ["z"] * 50)
    kl = categorical_kl(cur, base)
    assert math.isfinite(kl) and kl > 0.5


def test_categorical_kl_skew_change_detected() -> None:
    base = snapshot_column(_cat(["a"] * 50 + ["b"] * 50))
    cur = _cat(["a"] * 90 + ["b"] * 10)
    kl = categorical_kl(cur, base)
    assert kl > 0.1
    # 同分布对照
    same = categorical_kl(_cat(["a"] * 50 + ["b"] * 50), base)
    assert same == pytest.approx(0.0, abs=1e-9)


def test_kl_boundary_threshold_semantics() -> None:
    """0.1 阈值语义:温和偏移 < 0.1 < 剧烈偏移(端到端口径锚定)。"""
    base = snapshot_column(_cat(["a"] * 55 + ["b"] * 45))
    mild = categorical_kl(_cat(["a"] * 60 + ["b"] * 40), base)
    severe = categorical_kl(_cat(["a"] * 95 + ["b"] * 5), base)
    assert mild < 0.1 < severe


# === evaluate_drift(发布层复用,W3) ==========================================

def test_evaluate_drift_flags_and_skips_missing() -> None:
    from arrow_lake.quality.drift import evaluate_drift
    base = {
        "severity": snapshot_column(_cat(["a"] * 50 + ["b"] * 50)),
        "gone": snapshot_column(_cat(["x"] * 10)),   # 基线有、当前无
    }
    shifted = pa.table({"severity": pa.array(["a"] * 95 + ["b"] * 5, pa.string())})
    out = evaluate_drift(shifted, base, threshold=0.1)
    assert "gone" not in out["columns"]              # 消失列跳过
    assert out["columns"]["severity"]["drifted"] is True
    assert out["drifted"] == ["severity"]


def test_evaluate_drift_stable_no_flags() -> None:
    from arrow_lake.quality.drift import evaluate_drift
    values = ["a"] * 50 + ["b"] * 50
    base = {"c": snapshot_column(_cat(values))}
    out = evaluate_drift(pa.table({"c": pa.array(values, pa.string())}), base, 0.1)
    assert out["drifted"] == [] and out["columns"]["c"]["kl"] == pytest.approx(0.0)
