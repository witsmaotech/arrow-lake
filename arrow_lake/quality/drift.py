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
    "evaluate_drift",
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


def _vc_pairs(arr: pa.ChunkedArray | pa.Array) -> tuple[list[Any], list[int]]:
    """value_counts → (values, counts) 两个短 Python 列表(只对低基数用)。"""
    vc = pc.value_counts(arr)
    return (vc.field("values").to_pylist(),
            vc.field("counts").to_pylist())  # type: ignore[return-value]


def _numeric_counts(nn: pa.ChunkedArray, lo: float, hi: float) -> list[int]:
    """等宽 32 桶计数——**向量化**(四维 review H9:原逐行 Python 分桶在
    107M 行级 = 每列 GB 级物化 + 10-25s 纯 Python;现 O(n) Arrow 内核,
    只物化 ≤32 个桶计数)。语义与原 ``_bin_index`` 循环一致(值 ≥ lo 时
    int() 截断 ≡ floor;越界 clamp 首末桶;退化区间全落桶 0)。"""
    counts = [0] * NUMERIC_BINS
    if len(nn) == 0:
        return counts
    if hi <= lo:
        counts[0] = len(nn)
        return counts
    scale = (hi - lo) / NUMERIC_BINS
    # 统一 float64:整数列若用 nn.type 标量,scale 会被截断成 0 → 除零
    f = pc.cast(nn, pa.float64())
    idx = pc.divide(pc.subtract(f, pa.scalar(lo, pa.float64())),
                    pa.scalar(scale, pa.float64()))
    idx = pc.cast(pc.floor(idx), pa.int64())
    idx = pc.max_element_wise(idx, pa.scalar(0, pa.int64()))
    idx = pc.min_element_wise(idx, pa.scalar(NUMERIC_BINS - 1, pa.int64()))
    for v, c in zip(*_vc_pairs(idx), strict=True):
        counts[int(v)] = int(c)
    return counts


# --------------------------------------------------------------------------- #
# 快照
# --------------------------------------------------------------------------- #

def snapshot_column(col: pa.ChunkedArray) -> dict[str, Any] | None:
    """单列快照。numeric → 直方图;类别 → 频率+other。

    数值列先过滤 NaN/inf(非有限值不参与 min/max 与分桶——原实现
    ``int(NaN)`` 抛 ValueError 使发布基线静默缺失/重置 500,L6);
    过滤后全空 → None(调用方跳过该列)。
    """
    nn = _non_null(col)
    if _is_numeric(nn.type):
        nn = pc.filter(nn, pc.is_finite(nn))
        if len(nn) == 0:
            return None
        lo = float(pc.min(nn).as_py())
        hi = float(pc.max(nn).as_py())
        return {
            "kind": "numeric", "min": lo, "max": hi,
            "bins": NUMERIC_BINS, "counts": _numeric_counts(nn, lo, hi),
        }
    # categorical:top-K 排序在 Arrow 层完成(高基数列不物化全量频率)
    vc = pc.value_counts(nn)
    table = pa.table({
        "values": vc.field("values"), "counts": vc.field("counts"),
    }).sort_by([("counts", "descending")])
    top_rows = table.slice(0, min(CATEGORY_TOP, table.num_rows)).to_pylist()
    total = int(pc.sum(table.column("counts")).as_py() or 0)
    top = {str(r["values"]): int(r["counts"]) for r in top_rows}
    return {
        "kind": "categorical", "values": top,
        "other": int(total - sum(top.values())),
        "total": int(total),
    }


def snapshot_table(table: pa.Table) -> dict[str, dict[str, Any]]:
    """全表快照(数值+类别列;全空/全 NaN/其他类型列跳过)。"""
    out: dict[str, dict[str, Any]] = {}
    for name in table.column_names:
        col = table.column(name)
        if not (_is_numeric(col.type) or _is_categorical(col.type)):
            continue
        if len(col) == col.null_count:
            continue
        snap = snapshot_column(col)
        if snap is not None:
            out[name] = snap
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
    """数值列漂移:当前值按基线边界 rebin → KL(向量化分桶)。空列 → 0.0。"""
    nn = _non_null(current)
    nn = pc.filter(nn, pc.is_finite(nn))
    if len(nn) == 0:
        return 0.0
    lo, hi = float(baseline["min"]), float(baseline["max"])
    p = _numeric_counts(nn, lo, hi)
    return _kl([float(x) for x in p], [float(c) for c in baseline["counts"]])


def categorical_kl(current: pa.ChunkedArray, baseline: dict[str, Any]) -> float:
    """类别列漂移:当前值映射基线类目(未知名→other 桶)→ KL。

    向量化(H9):``pc.index_in`` 批量映射替代逐行 Python dict 查表;
    other = 总数 − 命中计数(null 行不产计数,与原语义一致)。
    """
    nn = _non_null(current)
    if len(nn) == 0:
        return 0.0
    if pa.types.is_dictionary(nn.type):
        nn = pc.cast(nn, pa.string())
    base_values: dict[str, int] = dict(baseline["values"])
    names = list(base_values)
    if not names:
        return 0.0
    p = [0] * (len(names) + 1)   # named + other
    idx = pc.index_in(nn, value_set=pa.array(names, type=nn.type))
    named_total = 0
    for v, c in zip(*_vc_pairs(idx), strict=True):
        if v is not None:
            p[int(v)] += int(c)
            named_total += int(c)
    p[-1] = len(nn) - named_total
    q = [float(base_values[v]) for v in names] + [float(baseline["other"])]
    # 双方都无 other 质量时丢弃该桶——观测分布的 KL 精确为 0(ε 不入
    # 归一化);有新值时保留(other 以 ε 地板参与,见 _kl)。
    if q[-1] == 0 and p[-1] == 0:
        p, q = p[:-1], q[:-1]
    return _kl([float(x) for x in p], q)


def evaluate_drift(
    table: pa.Table,
    baseline_columns: dict[str, Any],
    threshold: float,
) -> dict[str, Any]:
    """逐列 KL 评估(assess 漂移节 + 发布层超限拒共用,W3)。

    基线有、当前 schema 无的列跳过;返回 ``{columns, drifted}``。
    """
    columns: dict[str, Any] = {}
    drifted: list[str] = []
    for name, base in baseline_columns.items():
        if name not in table.column_names:
            continue
        col = table.column(name)
        if base.get("kind") == "numeric":
            kl = numeric_kl(col, base)
        elif base.get("kind") == "categorical":
            kl = categorical_kl(col, base)
        else:  # pragma: no cover — 快照只产这两种 kind
            continue
        is_drift = kl > threshold
        columns[name] = {"kl": round(kl, 6), "kind": base["kind"], "drifted": is_drift}
        if is_drift:
            drifted.append(name)
    return {"columns": columns, "drifted": drifted}
