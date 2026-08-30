"""W1.1 — 契约 ``quality:`` 节解析 + 默认常量(v1.11.4 MS5)。

QoS Annotation 形态(DR15/S1):五维权重/阈值/否决/准入作为契约顶层
``quality:`` 节,**登记不校验**——沿 MS2 label / lifecycle 先例,compiler
产出零变化(入口门不消费)。缺省 = 业务手册默认值(引擎内置常量),
契约不写即默认,零配置可用。

覆盖:默认/覆盖/非法权重/缺字段/归一化/critical/features diff。
"""

from __future__ import annotations

import pytest
from arrow_lake.contract.compiler import compile_contract
from arrow_lake.contract.schema import parse_contract
from arrow_lake.quality.spec import (
    DEFAULT_ADMISSION,
    DEFAULT_THRESHOLDS,
    DEFAULT_WEIGHTS,
    resolve_quality_spec,
)

BASE = """
dataset: gas_net
tables:
  segments:
    columns:
      - name: material
        enum: [PE, steel]
"""


def _parse(extra: str):
    return parse_contract(BASE + extra)


# --- 默认(契约不写 quality 节) ----------------------------------------------

def test_absent_quality_node_defaults() -> None:
    contract = _parse("")
    assert contract.quality is None
    spec = resolve_quality_spec(contract.quality)
    assert spec.weights == DEFAULT_WEIGHTS
    assert DEFAULT_WEIGHTS == {
        "relevance": 0.20, "accuracy": 0.35, "completeness": 0.20,
        "diversity": 0.15, "timeliness": 0.10,
    }
    assert spec.critical is False
    assert spec.max_p95_hours == 72
    assert spec.admission == DEFAULT_ADMISSION == (85, 95, 98)
    assert spec.thresholds == DEFAULT_THRESHOLDS
    assert spec.thresholds["accuracy"] == 81 and spec.thresholds["relevance"] == 90


# --- 覆盖(完整 quality 节 round-trip) ---------------------------------------

def test_full_quality_node_roundtrip() -> None:
    contract = _parse("""
quality:
  weights: {relevance: 0.1, accuracy: 0.5, completeness: 0.2, diversity: 0.1, timeliness: 0.1}
  thresholds: {accuracy: 90, relevance: 95}
  veto: [accuracy_below_threshold]
  admission: {bronze: 80, silver: 90, gold: 96}
  timeliness: {max_p95_hours: 24}
  critical: true
""")
    q = contract.quality
    assert q is not None
    assert q.weights is not None and q.weights["accuracy"] == 0.5
    assert q.thresholds is not None and q.thresholds["relevance"] == 95
    assert q.veto == ("accuracy_below_threshold",)
    assert q.admission is not None and q.admission.gold == 96
    assert q.timeliness is not None and q.timeliness.max_p95_hours == 24
    assert q.critical is True


# --- 非法值(解析期拒绝 → 契约保存 422) --------------------------------------

@pytest.mark.parametrize("quality_yaml", [
    "quality:\n  weights: {relevance: -0.2, accuracy: 0.5, completeness: 0.2, diversity: 0.1, timeliness: 0.1}\n",  # 负权重
    "quality:\n  weights: {relevance: 0.2, bogus: 0.5, completeness: 0.2, diversity: 0.1, timeliness: 0.1}\n",  # 未知维度
    "quality:\n  weights: {}\n",  # 空权重(全零无法归一)
    "quality:\n  thresholds: {accuracy: 150}\n",  # 阈值越界
    "quality:\n  admission: {bronze: 95, silver: 85, gold: 98}\n",  # bronze > silver
    "quality:\n  timeliness: {max_p95_hours: 0}\n",  # 非正时效参数
])
def test_invalid_quality_node_rejected(quality_yaml: str) -> None:
    with pytest.raises(ValueError):
        _parse(quality_yaml)


# --- 缺字段(逐字段回落默认) --------------------------------------------------

def test_partial_node_field_defaults() -> None:
    contract = _parse("quality:\n  critical: true\n")
    spec = resolve_quality_spec(contract.quality)
    # 未写的字段逐项回落默认
    assert spec.weights == DEFAULT_WEIGHTS
    assert spec.admission == (85, 95, 98)
    assert spec.max_p95_hours == 72
    # critical 单独生效
    assert spec.critical is True
    assert spec.thresholds["accuracy"] == 95  # critical → 准确性门槛抬高


# --- 归一化(权重和 ≠ 1 时按比例归一) ----------------------------------------

def test_weight_normalization() -> None:
    contract = _parse(
        "quality:\n  weights: {relevance: 2, accuracy: 4, completeness: 2, "
        "diversity: 1, timeliness: 1}\n"
    )
    spec = resolve_quality_spec(contract.quality)
    assert sum(spec.weights.values()) == pytest.approx(1.0)
    assert spec.weights["accuracy"] == pytest.approx(0.4)
    assert spec.weights["relevance"] == pytest.approx(0.2)


# --- critical 语义 -----------------------------------------------------------

def test_critical_raises_accuracy_threshold() -> None:
    spec = resolve_quality_spec(None)
    assert spec.thresholds["accuracy"] == 81
    from arrow_lake.contract.schema import QualitySpec
    spec95 = resolve_quality_spec(QualitySpec(critical=True))
    assert spec95.thresholds["accuracy"] == 95


# --- 登记不校验:compiler 产出零变化 ------------------------------------------

def test_compiler_ignores_quality_node() -> None:
    plain = compile_contract(_parse(""))
    with_q = compile_contract(_parse(
        "quality:\n  weights: {relevance: 0.2, accuracy: 0.35, completeness: 0.2, "
        "diversity: 0.15, timeliness: 0.1}\n  veto: [accuracy_below_threshold]\n"
    ))
    assert plain.rows == with_q.rows
    assert plain.references == with_q.references


# --- 版本链 diff 可见(features 含 quality) ----------------------------------

def test_features_include_quality_for_diff() -> None:
    from arrow_lake.contract.schema import contract_features, diff_features
    old = contract_features(_parse(""))
    new = contract_features(_parse("quality:\n  critical: true\n"))
    diff = diff_features(old, new)
    assert diff.get("quality") == {"was": None, "now": {"critical": True}}
