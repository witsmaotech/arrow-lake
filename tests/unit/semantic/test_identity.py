"""W2.2 (v1.11.1 F2.1) — 对象标识解析。

对象 ID = 契约 identifier 列值(规范形态本身即身份);``parse_identifier``
用契约 pattern 的 extract 形态抽命名组件({区域}/{序列}),不合规值**标记
不炸**(matched=False 携带原值,交上层决定死信/告警)。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from arrow_lake.contract.schema import parse_contract
from arrow_lake.semantic.identity import (
    parse_identifier,
    parse_table_identifier,
)


class TestParseIdentifier:
    def test_named_groups_extracted(self) -> None:
        # default group translation [^.]+ → groups are DOT-separated; ids with
        # dash-separated tails need an explicit group regex (see test below).
        p = parse_identifier("GAS.SEGMENT.{区域}.{序列}", "GAS.SEGMENT.RG01.S047")
        assert p.matched is True
        assert p.object_id == "GAS.SEGMENT.RG01.S047"
        assert p.components == {"区域": "RG01", "序列": "S047"}

    def test_dash_separated_tail_needs_explicit_regex(self) -> None:
        # the plan-doc sample id GAS.SEGMENT.RG01-001-S047 only conforms when
        # the pattern declares the dash explicitly — good pattern-authoring
        # guidance surfaced by the default-translation semantics.
        assert parse_identifier(
            "GAS.SEGMENT.{区域:[A-Z0-9]+}-{序列}", "GAS.SEGMENT.RG01-001-S047",
        ).components == {"区域": "RG01", "序列": "001-S047"}

    def test_custom_regex_group(self) -> None:
        assert parse_identifier("V-{ver:[0-9]+}", "V-42").components == {"ver": "42"}
        assert parse_identifier("V-{ver:[0-9]+}", "V-4x2").matched is False

    def test_nonconforming_marks_not_raises(self) -> None:
        p = parse_identifier("GAS.SEGMENT.{区域}.{序列}", "乱写的值")
        assert p.matched is False
        assert p.components == {}
        assert p.object_id == "乱写的值"

    def test_literal_only_pattern(self) -> None:
        assert parse_identifier("ID-001", "ID-001").matched is True
        assert parse_identifier("ID-001", "ID-002").matched is False

    def test_unicode_group_names(self) -> None:
        p = parse_identifier("{域}.{类型}.{序号}", "GAS.管段.001")
        assert p.matched is True
        assert p.components == {"域": "GAS", "类型": "管段", "序号": "001"}

    def test_partial_match_is_not_a_match(self) -> None:
        # full-match semantics mirror the gate's regexp_full_match
        assert parse_identifier("GAS.{a}.{b}", "GAS.only-one").matched is False


class TestParseTableIdentifier:
    CONTRACT_YAML = """
dataset: gas_net
tables:
  segments:
    identifier:
      column: seg_id
      pattern: "GAS.SEGMENT.{区域}.{序列}"
    columns: []
  stations:
    columns: []
"""

    def _contract(self):
        return parse_contract(self.CONTRACT_YAML)

    def test_resolves_via_table_section(self) -> None:
        p = parse_table_identifier(self._contract(), "segments", "GAS.SEGMENT.R01.S1")
        assert p is not None and p.matched is True
        assert p.components["区域"] == "R01"

    def test_no_identifier_rule_returns_none(self) -> None:
        assert parse_table_identifier(self._contract(), "stations", "X") is None

    def test_unknown_table_returns_none(self) -> None:
        assert parse_table_identifier(self._contract(), "valves", "X") is None


class TestReDoSCapInherited:
    def test_oversized_group_regex_rejected_at_contract_save(self) -> None:
        # the 256-char group cap is enforced by the contract schema (W1/P2-5);
        # parse_identifier therefore can never receive a pathological pattern
        # that reached it through a saved contract.
        bad = "V-{v:" + "a" * 257 + "}"
        with pytest.raises(ValidationError):
            parse_contract(f"""
dataset: d
tables:
  t:
    identifier: {{column: x, pattern: "{bad}"}}
    columns: []
""")
