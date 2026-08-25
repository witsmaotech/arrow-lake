"""W2.2 contract schema — table-section model, pattern syntax, schema check.

DR14/v1.11.0.1: a dataset contract is one-document-per-dataset(container);
``tables:`` holds one section per table (a legacy single-table contract with
a top-level ``ontology:`` block auto-wraps into exactly one section).
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from arrow_lake.contract.schema import (
    Severity,
    parse_contract,
    pattern_to_extract_regex,
    pattern_to_match_regex,
)

CONTAINER_YAML = """
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
      - name: material
        enum: [PE, steel, ductile_iron]
      - name: commissioned_on
        type: date
  stations:
    object_class: 场站
    columns:
      - name: name
        required: true
references:
  - {from: segments.station_id, to: stations.id}
  - {from: segments.owner_org, to_dataset: gas_orgs, to_column: org_id}
"""

LEGACY_YAML = """
dataset: gas_segments
ontology:
  object_class: 管段
  identifier:
    column: seg_id
    pattern: "GAS.SEG.{n}"
  columns:
    - name: material
      enum: [PE, steel]
  references:
    - {column: station_id, to_dataset: gas_stations, to_column: id}
"""


class TestParseContract:
    def test_container_contract_parses(self) -> None:
        c = parse_contract(CONTAINER_YAML)
        assert c.dataset == "gas_net"
        assert set(c.tables) == {"segments", "stations"}
        assert c.tables["segments"].object_class == "管段"
        assert c.tables["segments"].identifier is not None
        assert c.tables["segments"].identifier.column == "seg_id"
        assert c.tables["stations"].columns[0].name == "name"
        assert c.tables["stations"].columns[0].required is True

    def test_legacy_singular_wraps_default_section(self) -> None:
        c = parse_contract(LEGACY_YAML)
        assert set(c.tables) == {"gas_segments"}
        assert c.tables["gas_segments"].identifier.pattern == "GAS.SEG.{n}"
        # legacy reference normalized: from = {dataset}.{column}
        ref = c.references[0]
        assert ref.from_table == "gas_segments"
        assert ref.from_column == "station_id"
        assert ref.to_dataset == "gas_stations"
        assert ref.to_column == "id"

    def test_references_parse_both_forms(self) -> None:
        c = parse_contract(CONTAINER_YAML)
        intra, cross = c.references
        assert (intra.from_table, intra.from_column) == ("segments", "station_id")
        assert (intra.to_table, intra.to_column) == ("stations", "id")
        assert intra.to_dataset is None
        assert cross.to_dataset == "gas_orgs"
        assert cross.to_table is None

    def test_invalid_table_name_rejected(self) -> None:
        bad = CONTAINER_YAML.replace("  stations:", "  sta tions:")
        with pytest.raises(ValueError):
            parse_contract(bad)

    def test_bad_range_rejected(self) -> None:
        bad = CONTAINER_YAML.replace("range: [0, 4000]", "range: [4000, 0]")
        with pytest.raises(ValueError):
            parse_contract(bad)

    def test_bad_pattern_syntax_rejected(self) -> None:
        # unclosed brace → pattern syntax error surfaces as ValueError
        bad = CONTAINER_YAML.replace('pattern: "GAS.SEGMENT.{区域}.{序列}"',
                                     'pattern: "GAS.SEGMENT.{区域"')
        with pytest.raises(ValueError):
            parse_contract(bad)

    def test_empty_enum_rejected(self) -> None:
        bad = CONTAINER_YAML.replace("enum: [PE, steel, ductile_iron]", "enum: []")
        with pytest.raises(ValueError):
            parse_contract(bad)

    def test_dataset_name_required(self) -> None:
        with pytest.raises(ValueError):  # plain ValueError; ValidationError is a subclass
            parse_contract("tables: {}\n")


class TestPatternSyntax:
    def test_match_regex_literals_escaped(self) -> None:
        # validation form: literals escaped, NO capture groups (DuckDB RE2)
        rx = pattern_to_match_regex("A.B-{x}")
        assert rx == r"A\.B\-[^.]+"

    def test_named_group_default_translation(self) -> None:
        import re

        # extraction form (F2.1): named groups, default [^.]+ translation
        rx = pattern_to_extract_regex("GAS.SEGMENT.{区域}.{序列}")
        m = re.fullmatch(rx, "GAS.SEGMENT.east.0012")
        assert m is not None and m.group("区域") == "east"

    def test_explicit_group_regex(self) -> None:
        import re

        rx = pattern_to_match_regex("V-{ver:[0-9]+}")
        assert re.fullmatch(rx, "V-12") is not None
        assert re.fullmatch(rx, "V-ab") is None

    def test_extract_and_match_agree(self) -> None:
        # both compilations accept the same strings (validation vs F2.1 parse)
        import re

        mrx = pattern_to_match_regex("GAS.SEGMENT.{区域}.{序列}")
        erx = pattern_to_extract_regex("GAS.SEGMENT.{区域}.{序列}")
        s = "GAS.SEGMENT.north.42"
        assert re.fullmatch(mrx, s) is not None
        assert re.fullmatch(erx, s) is not None

    def test_unclosed_brace_raises(self) -> None:
        with pytest.raises(ValueError):
            pattern_to_match_regex("{oops")

    def test_nested_brace_raises(self) -> None:
        with pytest.raises(ValueError):
            pattern_to_match_regex("{a:{b}}")


class TestSeverityAnnotation:
    def test_row_constraint_severities(self) -> None:
        c = parse_contract(CONTAINER_YAML)
        seg = c.tables["segments"]
        by_name = {r.name: r for r in seg.columns}
        # domain constraints are reject-level; type assertion is warn-level
        assert by_name["material"].severity == Severity.REJECT
        assert by_name["pressure"].severity == Severity.REJECT
        assert by_name["commissioned_on"].severity == Severity.WARN
        assert seg.identifier is not None

    def test_unit_is_registration_only(self) -> None:
        c = parse_contract(CONTAINER_YAML)
        col = {r.name: r for r in c.tables["segments"].columns}["pressure"]
        assert col.unit == "kPa"
        # unit carries no row constraint — compiler must skip it (no SQL)


class TestCheckAgainstSchema:
    def test_unknown_column_warns(self) -> None:
        c = parse_contract(CONTAINER_YAML)
        schema = pa.schema([("seg_id", pa.string()), ("pressure", pa.float64())])
        notes = c.check_against_schema("segments", schema)
        unknown = [n for n in notes if n["kind"] == "unknown_column"]
        assert {n["column"] for n in unknown} == {"material", "commissioned_on"}
        assert all(n["level"] == Severity.WARN for n in unknown)

    def test_type_mismatch_warns(self) -> None:
        c = parse_contract(CONTAINER_YAML)
        schema = pa.schema([
            ("seg_id", pa.string()), ("pressure", pa.float64()),
            ("material", pa.string()), ("commissioned_on", pa.string()),  # not date
        ])
        notes = c.check_against_schema("segments", schema)
        mism = [n for n in notes if n["kind"] == "type_mismatch"]
        assert len(mism) == 1 and mism[0]["column"] == "commissioned_on"

    def test_clean_schema_no_notes(self) -> None:
        c = parse_contract(CONTAINER_YAML)
        schema = pa.schema([
            ("seg_id", pa.string()), ("pressure", pa.float64()),
            ("material", pa.string()), ("commissioned_on", pa.date32()),
        ])
        assert c.check_against_schema("segments", schema) == []

    def test_unknown_table_empty(self) -> None:
        c = parse_contract(CONTAINER_YAML)
        assert c.check_against_schema("ghost", pa.schema([("a", pa.int64())])) == []
