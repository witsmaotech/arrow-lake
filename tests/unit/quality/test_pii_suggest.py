"""PII 分级自动建议(v1.11.6.5,搁置 W2 #3 复活)——确定性扫描模块。

覆盖:模式匹配器(身份证 GB11643 校验位/手机号词边界/银行卡 Luhn/负例)、
列名语义(含 org_name 负面清单)、档位聚合(high→restricted 取最高/量门槛/
零证据 public)、suggest_classification 端到端(采样 cap/脱敏样本/自由文本
提示)。全确定性零模型;DoD 对照 lpg_danger 形态(坐标+区划列→confidential)。
"""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest

from arrow_lake.quality.pii_suggest import (
    _aggregate_tier,
    _Evidence,
    _id_card_checksum_ok,
    _luhn_ok,
    _mask,
    _scan_string_column,
    suggest_classification,
)

# 11010519491231002X:校验位合法(GB11643 mod 11-2 手算核对);改尾码即非法。
_VALID_ID = "11010519491231002X"
_VALID_CARD = "4111111111111111"  # Visa 测试卡号,Luhn 通过


def _fake_lance_table(t: pa.Table) -> SimpleNamespace:
    """lancedb Table 最小仿真:schema + to_lance().scanner(columns, limit)。"""

    def _scanner(columns=None, limit=None):
        sel = t.select(columns) if columns else t
        return SimpleNamespace(
            to_table=lambda: sel.slice(0, limit) if limit else sel
        )

    return SimpleNamespace(
        schema=t.schema, to_lance=lambda: SimpleNamespace(scanner=_scanner)
    )


class _FakeStorage:
    def __init__(self, table: pa.Table) -> None:
        self._table = table

    def open_dataset(self, name, *, table=None):
        return _fake_lance_table(self._table)


# ── 校验器 ────────────────────────────────────────────────────────────────


def test_id_card_checksum() -> None:
    assert _id_card_checksum_ok(_VALID_ID)
    assert not _id_card_checksum_ok(_VALID_ID[:-1] + "1")  # 校验位错
    assert not _id_card_checksum_ok("11010519491331002X")  # 非法月份(13)
    assert not _id_card_checksum_ok("12345")  # 长度不足


def test_luhn() -> None:
    assert _luhn_ok(_VALID_CARD)
    assert not _luhn_ok(_VALID_CARD[:-1] + "2")


# ── 内容扫描 ──────────────────────────────────────────────────────────────


def test_scan_id_card_ignores_bad_checksum() -> None:
    values = [_VALID_ID, _VALID_ID, _VALID_ID, _VALID_ID[:-1] + "1"]
    found = {e.key: e for e in _scan_string_column("x", values)}
    assert found["id_card"].hits == 3
    assert found["id_card"].severity == "high"
    assert all("***" in s for s in found["id_card"].masked_samples)  # 样本脱敏


def test_scan_phone_respects_digit_boundaries() -> None:
    # 21 位数字长串内不应命中 11 位手机号(词边界)
    found = _scan_string_column("x", ["13812345678", "9" * 21, "uid12313812345678x"])
    assert {e.key for e in found} == {"phone"}


def test_scan_email_and_mask() -> None:
    found = {e.key: e for e in _scan_string_column("x", ["a@b.com"] * 5)}
    assert found["email"].severity == "low"
    assert _mask("someone@example.com") == "some***om"


# ── 列名语义 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("column", "key", "severity"),
    [
        ("longitude", "geo_name", "medium"),
        ("latitude", "geo_name", "medium"),
        ("street", "address_name", "medium"),
        ("district", "address_name", "medium"),
        ("contact_phone", "phone_name", "low"),
        ("id_card_no", "id_card_name", "high"),
    ],
)
def test_column_semantics_hits(column, key, severity) -> None:
    from arrow_lake.quality.pii_suggest import _column_semantic

    ev = _column_semantic(column)
    assert ev is not None and ev.key == key and ev.severity == severity


@pytest.mark.parametrize(
    "column", ["org_name", "org_id", "uid", "img_url", "danger_level", "element"],
)
def test_column_semantics_excludes_non_pii_names(column) -> None:
    from arrow_lake.quality.pii_suggest import _column_semantic

    assert _column_semantic(column) is None  # 单位/追踪/枚举列不是自然人 PII


# ── 档位聚合 ──────────────────────────────────────────────────────────────


def _ev(severity: str, *, hits: int = 0, ratio: float | None = None) -> _Evidence:
    return _Evidence(
        column="c", key="k", label="l", severity=severity,
        hits=hits, hit_ratio=ratio,
    )


def test_aggregate_tier_ordering() -> None:
    assert _aggregate_tier([_ev("low"), _ev("medium")]) == "confidential"
    assert _aggregate_tier([_ev("high", hits=3, ratio=0.5)]) == "restricted"
    assert _aggregate_tier([_ev("low")]) == "internal"
    assert _aggregate_tier([]) == "public"


def test_aggregate_tier_quantity_gate() -> None:
    # 内容命中 2 行(<3 行且 <1%)不足以定 confidential → 回落 public
    assert _aggregate_tier([_ev("medium", hits=2, ratio=0.004)]) == "public"
    # 3 行绝对数达标
    assert _aggregate_tier([_ev("medium", hits=3, ratio=0.006)]) == "confidential"
    # 列名证据(hit_ratio=None)恒有效
    assert _aggregate_tier([_ev("medium")]) == "confidential"


# ── 端到端 ────────────────────────────────────────────────────────────────


def test_suggest_end_to_end_lpg_danger_shape() -> None:
    """DoD 对照:坐标+区划列(位置敏感)→ confidential;自由文本提示。"""
    t = pa.table(
        {
            "uid": ["a1f" + str(i) for i in range(10)],
            "district": ["蜀山区"] * 10,
            "street": ["XX路"] * 10,
            "longitude": [117.2 + i * 0.01 for i in range(10)],
            "latitude": [31.8 + i * 0.01 for i in range(10)],
            "org_name": [f"燃气公司{i}" for i in range(10)],
            "hazard_desc": ["阀门泄漏" * 20] * 10,  # 平均 80 字 → 自由文本
        }
    )
    out = suggest_classification(_FakeStorage(t), "lpg_like")
    assert out["suggested_tier"] == "confidential"
    assert out["engine"] == "rule_based"
    keys = {r["evidence"] for r in out["reasons"]}
    assert {"geo_name", "address_name"} <= keys
    assert "org_name" not in {r["column"] for r in out["reasons"]}  # 负面清单生效
    assert any("hazard_desc" in h for h in out["hints"])


def test_suggest_restricted_on_id_cards() -> None:
    t = pa.table({"备注": [_VALID_ID] * 5 + ["正常"] * 5})
    out = suggest_classification(_FakeStorage(t), "ds")
    assert out["suggested_tier"] == "restricted"
    ev = next(r for r in out["reasons"] if r["evidence"] == "id_card")
    assert ev["hits"] == 5 and ev["hit_ratio"] == 0.5


def test_suggest_public_on_clean_data() -> None:
    t = pa.table({"state": ["已整改"] * 10, "level": ["重大"] * 10})
    out = suggest_classification(_FakeStorage(t), "ds")
    assert out["suggested_tier"] == "public" and out["reasons"] == []


def test_suggest_samples_rows_and_masks() -> None:
    t = pa.table({"tel": [f"138{i:08d}" for i in range(1000)]})
    out = suggest_classification(_FakeStorage(t), "ds", sample_rows=100)
    assert out["scanned_rows"] == 100  # 采样 cap
    ev = next(r for r in out["reasons"] if r["evidence"] == "phone")
    assert ev["hit_ratio"] == 1.0 and len(ev["masked_samples"]) <= 3


def test_suggest_numeric_columns_only_name_semantics() -> None:
    """数值列不做内容扫描(坐标值本身不是字符串),仅列名语义。"""
    t = pa.table({"lon": pa.array([117.0], type=pa.float64())})
    out = suggest_classification(_FakeStorage(t), "ds")
    assert out["suggested_tier"] == "confidential"  # geo_name 列名证据
    assert all(r["hit_ratio"] is None for r in out["reasons"])
