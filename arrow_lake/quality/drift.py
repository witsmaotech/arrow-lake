"""F5.3 — 漂移监控:列快照 + KL 散度(v1.11.4 MS5 W2.2)。

旁路纯函数(设计 §5 / S6):
* **快照**:数值列(integer/floating,含时间戳以外的数值类型)等宽
  ``NUMERIC_BINS=32`` 桶直方图(min/max/counts);类别列(string/
  dictionary)``CATEGORY_TOP=32`` 频率 + **other 桶**(基线未见的新值
  检测期落 other)。temporal 列不入快照(新鲜度归 timeliness 维度)。
* **KL(P‖Q)**:P=当前数据(按**基线边界** rebin;数值越界 clamp 进
  首末桶),Q=基线分布。**平滑只加在未观测桶**(Qᵢ=0 → ε),观测质量
  精确保留——同分布 KL 精确为 0,新值集中仍有限(不 ∞)。
* 阈值默认 ``DEFAULT_DRIFT_KL=0.1``(契约 ``quality.drift_kl`` 覆盖)。

基线存储 = ``sys_drift_baselines``(V022,发布时自动快照 + 手动可重置,
W2.b);检测入口 = ``POST /quality/drift/{ds}``(W2.d)。
"""

from __future__ import annotations

import math
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

__all__ = [
    "CATEGORY_TOP",
    "DEFAULT_DRIFT_KL",
    "NUMERIC_BINS",
    "categorical_kl",
    "numeric_kl",
    "snapshot_column",
    "snapshot_table",
]

#: 数值列等宽桶数(S6)。
NUMERIC_BINS = 32
#: 类别列 top-K 频率(S6)。
CATEGORY_TOP = 32
#: KL 阈值默认(设计 §5:超阈 → 报告 + metrics)。
DEFAULT_DRIFT_KL = 0.1
#: 未观测桶的概率地板(防 log(∞);只对 Q=0 的桶生效)。
_KL_EPSILON = 1e-6


def _is_numeric(type_: pa.DataType) -> bool:
    return pa.types.is_integer(type_) or pa.types.is_floating(type_)


def _is_categorical(type_: pa.DataType) -> bool:
    return (
        pa.types.is_string(type_) or pa.types.is_large_string(type_)
        or pa.types.is_dictionary(type_)
    )


def _non_null(col: pa.ChunkedArray) -> pa.ChunkedArray:
    out = pc.drop_null(col)
    return out  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# 快照
# --------------------------------------------------------------------------- #

def snapshot_column(col: pa.ChunkedArray) -> dict[str, Any]:
    """单列快照(调用方保证非全空)。numeric → 直方图;类别 → 频率+other。"""
    nn = _non_null(col)
    if _is_numeric(nn.type):
        lo = float(pc.min(nn).as_py())
        hi = float(pc.max(nn).as_py())
        counts = [0] * NUMERIC_BINS
        for v in nn.to_pylist():
            counts[_bin_index(float(v), lo, hi)] += 1
        return {
            "kind": "numeric", "min": lo, "max": hi,
            "bins": NUMERIC_BINS, "counts": counts,
        }
    # categorical
    vc = pc.value_counts(nn)
    freqs = sorted(
        zip(vc.field("values").to_pylist(), vc.field("counts").to_pylist(),
            strict=True),
        key=lambda kv: (-kv[1], str(kv[0])),
    )
    top = {str(v): int(c) for v, c in freqs[:CATEGORY_TOP]}
    other = int(sum(c for _, c in freqs[CATEGORY_TOP:]))
    return {
        "kind": "categorical", "values": top, "other": other,
        "total": int(sum(c for _, c in freqs)),
    }


def snapshot_table(table: pa.Table) -> dict[str, dict[str, Any]]:
    """全表快照(数值+类别列;全空/其他类型列跳过)。"""
    out: dict[str, dict[str, Any]] = {}
    for name in table.column_names:
        col = table.column(name)
        if not (_is_numeric(col.type) or _is_categorical(col.type)):
            continue
        if col.null_count == col.num_chunks and len(col) == col.null_count:
            continue
        nn = _non_null(col)
        if len(nn) == 0:
            continue
        out[name] = snapshot_column(col)
    return out


# --------------------------------------------------------------------------- #
# KL 散度
# --------------------------------------------------------------------------- #

def _bin_index(value: float, lo: float, hi: float) -> int:
    """等宽桶号;越界 clamp 进首末桶;退化区间(max==min)全落桶 0。"""
    if hi <= lo:
        return 0
    idx = int((value - lo) / (hi - lo) * NUMERIC_BINS)
    return max(0, min(NUMERIC_BINS - 1, idx))


def _kl(p_counts: list[float], q_counts: list[float]) -> float:
    """KL(P‖Q),P/Q 归一化;Q 零桶以 ε 地板平滑(观测质量精确保留)。"""
    p_total = sum(p_counts)
    q_smoothed = [c if c > 0 else _KL_EPSILON for c in q_counts]
    q_total = sum(q_smoothed)
    if p_total <= 0 or q_total <= 0:
        return 0.0
    kl = 0.0
    for p_raw, q_raw in zip(p_counts, q_smoothed, strict=True):
        if p_raw <= 0:
            continue  # 0·log0 = 0
        p = p_raw / p_total
        q = q_raw / q_total
        kl += p * math.log(p / q)
    return kl


def numeric_kl(current: pa.ChunkedArray, baseline: dict[str, Any]) -> float:
    """数值列漂移:当前值按基线边界 rebin → KL。空当前列 → 0.0。"""
    nn = _non_null(current)
    if len(nn) == 0:
        return 0.0
    lo, hi = float(baseline["min"]), float(baseline["max"])
    p = [0] * NUMERIC_BINS
    for v in nn.to_pylist():
        p[_bin_index(float(v), lo, hi)] += 1
    return _kl(p, [float(c) for c in baseline["counts"]])


def categorical_kl(current: pa.ChunkedArray, baseline: dict[str, Any]) -> float:
    """类别列漂移:当前值映射基线类目(未知名→other 桶)→ KL。"""
    nn = _non_null(current)
    if len(nn) == 0:
        return 0.0
    base_values: dict[str, int] = dict(baseline["values"])
    names = list(base_values)
    p = [0] * (len(names) + 1)   # named + other
    idx_of = {v: i for i, v in enumerate(names)}
    for v in nn.to_pylist():
        key = str(v)
        if key in idx_of:
            p[idx_of[key]] += 1
        else:
            p[-1] += 1
    q = [float(base_values[v]) for v in names] + [float(baseline["other"])]
    # 双方都无 other 质量时丢弃该桶——观测分布的 KL 精确为 0(ε 不入
    # 归一化);有新值时保留(other 以 ε 地板参与,见 _kl)。
    if q[-1] == 0 and p[-1] == 0:
        p, q = p[:-1], q[:-1]
    return _kl(p, q)
