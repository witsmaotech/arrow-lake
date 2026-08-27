"""W1.1-W1.3 (v1.11.1/DR15 D-1) — contract schema extension.

``label`` (column), ``lifecycle`` (table section), ``cardinality``+``kind``
(references) are registration-only model-layer fields: they must parse,
validate, and flow into version-chain features/diffs — while generating NO
compiler constraints (gate semantics frozen, DR15 red line).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from arrow_lake.contract.compiler import compile_contract
from arrow_lake.contract.schema import (
    contract_features,
    diff_features,
    parse_contract,
)

BASE_YAML = """
dataset: gas_net
tables:
  segments:
    object_class: 管段
    identifier:
      column: seg_id
      pattern: "GAS.SEGMENT.{区域}.{序列}"
    columns:
      - name: pressure
        unit: kPa
        range: [0, 4000]
  stations:
    object_class: 场站
    columns:
      - name: name
references:
  - {from: segments.station_id, to: stations.id}
"""

EXTENDED_YAML = """
dataset: gas_net
tables:
  segments:
    object_class: 管段
    lifecycle:
      column: status
      states: [在建, 在运, 停用, 报废]
      initial: 在建
    identifier:
      column: seg_id
      pattern: "GAS.SEGMENT.{区域}.{序列}"
    columns:
      - name: pressure
        label: 管段运行压力
        unit: kPa
        range: [0, 4000]
  stations:
    object_class: 场站
    columns:
      - name: name
references:
  - {from: segments.station_id, to: stations.id, cardinality: N:1, kind: association}
"""


class TestBackwardCompat:
    def test_legacy_yaml_parses_with_new_fields_none(self) -> None:
        c = parse_contract(BASE_YAML)
        seg = c.tables["segments"]
        assert seg.lifecycle is None
        assert all(r.label is None for r in seg.columns)
        assert c.references[0].cardinality is None
        assert c.references[0].kind is None


class TestExtendedParse:
    def test_label_lifecycle_cardinality_kind_parse(self) -> None:
        c = parse_contract(EXTENDED_YAML)
        seg = c.tables["segments"]
        assert seg.columns[0].label == "管段运行压力"
        assert seg.lifecycle is not None
        assert seg.lifecycle.column == "status"
        assert seg.lifecycle.states == ("在建", "在运", "停用", "报废")
        assert seg.lifecycle.initial == "在建"
        ref = c.references[0]
        assert ref.cardinality == "N:1"
        assert ref.kind == "association"

    def test_lifecycle_without_optional_fields(self) -> None:
        c = parse_contract("""
dataset: d
tables:
  t:
    lifecycle: {states: [a, b]}
    columns: []
""")
        lc = c.tables["t"].lifecycle
        assert lc is not None
        assert lc.column is None and lc.initial is None
        assert lc.states == ("a", "b")


class TestValidation:
    def test_bad_cardinality_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_contract(BASE_YAML.replace(
                "references:\n  - {from: segments.station_id, to: stations.id}",
                'references:\n  - {from: segments.station_id, to: stations.id, cardinality: "2:N"}',
            ))

    def test_bad_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_contract(BASE_YAML.replace(
                "references:\n  - {from: segments.station_id, to: stations.id}",
                "references:\n  - {from: segments.station_id, to: stations.id, kind: inherits}",
            ))

    def test_empty_states_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_contract("""
dataset: d
tables:
  t:
    lifecycle: {states: []}
    columns: []
""")

    def test_duplicate_states_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_contract("""
dataset: d
tables:
  t:
    lifecycle: {states: [a, a]}
    columns: []
""")

    def test_initial_must_be_a_state(self) -> None:
        with pytest.raises(ValidationError):
            parse_contract("""
dataset: d
tables:
  t:
    lifecycle: {states: [a, b], initial: c}
    columns: []
""")

    def test_whitespace_label_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_contract("""
dataset: d
tables:
  t:
    columns:
      - name: x
        label: "   "
""")


class TestCompilerFrozen:
    """W1.3 — DR15 red line: extension fields generate NO compiler output."""

    def test_extended_fields_produce_identical_constraints(self) -> None:
        base = compile_contract(parse_contract(BASE_YAML))
        ext = compile_contract(parse_contract(EXTENDED_YAML))
        assert ext.rows == base.rows
        assert ext.references == base.references

    def test_no_new_constraint_kinds(self) -> None:
        bundle = compile_contract(parse_contract(EXTENDED_YAML))
        kinds = {c.kind for c in bundle.rows}
        assert kinds <= {"enum", "range", "pattern", "not_null"}


class TestFeaturesAndDiff:
    """W1.2 — label/lifecycle/cardinality+kind flow into version-chain diffs."""

    def test_features_carry_extension_fields(self) -> None:
        feats = contract_features(parse_contract(EXTENDED_YAML))
        seg = feats["tables"]["segments"]
        assert seg["columns"]["pressure"]["label"] == "管段运行压力"
        assert seg["lifecycle"] == ["status", ["在建", "在运", "停用", "报废"], "在建"]
        assert feats["references"][0][-2:] == ["N:1", "association"]

    def test_features_without_extensions(self) -> None:
        feats = contract_features(parse_contract(BASE_YAML))
        seg = feats["tables"]["segments"]
        assert seg["lifecycle"] is None
        assert seg["columns"]["pressure"]["label"] is None
        assert feats["references"][0][-2:] == ["", ""]

    def test_diff_shows_label_lifecycle_reference_changes(self) -> None:
        old = contract_features(parse_contract(BASE_YAML))
        new = contract_features(parse_contract(EXTENDED_YAML))
        diff = diff_features(old, new)
        seg = diff["tables"]["segments"]
        assert seg["lifecycle"] == {
            "was": None,
            "now": ["status", ["在建", "在运", "停用", "报废"], "在建"],
        }
        col_changes = {c["column"]: c for c in seg["columns"]}
        assert col_changes["pressure"]["change"] == "changed"
        assert col_changes["pressure"]["was"]["label"] is None
        assert col_changes["pressure"]["now"]["label"] == "管段运行压力"
        # reference gained cardinality/kind → old form removed, new form added
        assert not diff["references_removed"] or diff["references_removed"][0][-2:] == ["", ""]
        assert diff["references_added"][0][-2:] == ["N:1", "association"]

    def test_diff_shows_lifecycle_only_change(self) -> None:
        a = contract_features(parse_contract(EXTENDED_YAML))
        b = contract_features(parse_contract(EXTENDED_YAML.replace("initial: 在建", "initial: 在运")))
        diff = diff_features(a, b)
        assert diff["tables"]["segments"]["lifecycle"] == {
            "was": ["status", ["在建", "在运", "停用", "报废"], "在建"],
            "now": ["status", ["在建", "在运", "停用", "报废"], "在运"],
        }
        assert diff["tables"]["segments"].get("columns") is None
