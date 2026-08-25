"""W2.3 contract compiler — contract → DuckDB row predicates (D2: SQL/Arrow).

Every row constraint compiles to a SQL boolean where TRUE marks a VIOLATING
row (matches the W3 gate's dead-letter row-mask use). NULL semantics: domain
checks (enum/range/pattern) pass NULL rows; nullability is a separate
``required`` constraint. References compile to a NOT EXISTS template whose
target relation is resolved at validation time.
"""

from __future__ import annotations

import duckdb
import pyarrow as pa
import pytest

from arrow_lake.contract.compiler import compile_contract
from arrow_lake.contract.schema import parse_contract

CONTRACT_YAML = """
dataset: gas_net
tables:
  segments:
    identifier:
      column: seg_id
      pattern: "GAS.SEGMENT.{region}.{seq}"
    columns:
      - name: pressure
        range: [0, 4000]
      - name: material
        enum: [PE, steel]
      - name: note
        required: true
references:
  - {from: segments.station_id, to: stations.id}
"""

SEGMENTS = pa.table({
    "seg_id": ["GAS.SEGMENT.east.1", "BAD-ID", "GAS.SEGMENT.w.3", "GAS.SEGMENT.w.4", None],
    "pressure": [100.0, 200.0, 9999.0, None, 50.0],
    "material": ["PE", "steel", "PVC", None, "PE"],
    "note": ["a", "b", "c", "d", None],
    "station_id": ["S1", "S1", "S2", "S9", "S2"],
})

STATIONS = pa.table({"id": ["S1", "S2"], "name": ["east", "west"]})


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.register("segments", SEGMENTS)
    c.register("stations", STATIONS)
    yield c
    c.close()


def _violating_ids(con, sql: str) -> set[str]:
    rows = con.execute(f'SELECT seg_id FROM segments WHERE {sql}').fetchall()
    return {r[0] for r in rows}


class TestRowConstraints:
    def test_enum_compiles_and_hits(self, con) -> None:
        bundle = compile_contract(parse_contract(CONTRACT_YAML))
        c = bundle.row_constraint("segments", "material", "enum")
        assert _violating_ids(con, c.sql) == {"GAS.SEGMENT.w.3"}  # PVC; NULL passes

    def test_range_compiles_and_hits(self, con) -> None:
        bundle = compile_contract(parse_contract(CONTRACT_YAML))
        c = bundle.row_constraint("segments", "pressure", "range")
        assert _violating_ids(con, c.sql) == {"GAS.SEGMENT.w.3"}  # 9999; NULL passes

    def test_pattern_compiles_and_hits(self, con) -> None:
        bundle = compile_contract(parse_contract(CONTRACT_YAML))
        c = bundle.row_constraint("segments", "seg_id", "pattern")
        assert _violating_ids(con, c.sql) == {"BAD-ID"}  # format; NULL passes

    def test_not_null_compiles_and_hits(self, con) -> None:
        bundle = compile_contract(parse_contract(CONTRACT_YAML))
        c = bundle.row_constraint("segments", "note", "not_null")
        assert _violating_ids(con, c.sql) == {None}

    def test_unit_not_compiled(self) -> None:
        yml = CONTRACT_YAML.replace(
            "      - name: pressure\n        range: [0, 4000]",
            "      - name: pressure\n        unit: kPa\n        range: [0, 4000]",
        )
        bundle = compile_contract(parse_contract(yml))
        assert bundle.row_constraint("segments", "pressure", "pattern") is None
        # unit is registration-only: still exactly one pressure constraint (range)
        kinds = [c.kind for c in bundle.row_constraints("segments") if c.column == "pressure"]
        assert kinds == ["range"]

    def test_severity_is_reject_for_domain(self) -> None:
        bundle = compile_contract(parse_contract(CONTRACT_YAML))
        c = bundle.row_constraint("segments", "material", "enum")
        assert c.severity.value == "reject"

    def test_sql_injection_safe_literals(self) -> None:
        yml = CONTRACT_YAML.replace("enum: [PE, steel]", "enum: [\"O'Brien\", steel]")
        bundle = compile_contract(parse_contract(yml))
        c = bundle.row_constraint("segments", "material", "enum")
        assert "O''Brien" in c.sql  # single-quote escaped

    def test_constraints_grouped_by_table(self) -> None:
        bundle = compile_contract(parse_contract(CONTRACT_YAML))
        seg = bundle.row_constraints("segments")
        assert {(c.column, c.kind) for c in seg} == {
            ("seg_id", "pattern"), ("pressure", "range"),
            ("material", "enum"), ("note", "not_null"),
        }


class TestReferenceConstraints:
    def test_intra_container_reference(self, con) -> None:
        bundle = compile_contract(parse_contract(CONTRACT_YAML))
        refs = bundle.reference_constraints("segments")
        assert len(refs) == 1
        r = refs[0]
        assert (r.from_column, r.to_table, r.to_column) == ("station_id", "stations", "id")
        sql = r.render(target_relation="stations", source_alias="segments")
        # S9 is the only station_id missing from stations
        rows = con.execute(
            f'SELECT seg_id FROM segments WHERE {sql}'
        ).fetchall()
        assert {r0[0] for r0 in rows} == {"GAS.SEGMENT.w.4"}

    def test_cross_container_reference_shape(self) -> None:
        yml = CONTRACT_YAML.replace(
            "references:\n  - {from: segments.station_id, to: stations.id}\n",
            "references:\n  - {from: segments.station_id, to: stations.id}\n"
            "  - {from: segments.owner_org, to_dataset: gas_orgs, to_column: org_id}\n",
        )
        bundle = compile_contract(parse_contract(yml))
        refs = bundle.reference_constraints("segments")
        cross = [r for r in refs if r.to_dataset == "gas_orgs"]
        assert len(cross) == 1
        assert cross[0].to_table is None and cross[0].to_column == "org_id"
        # target relation for cross-container is resolved by the validator (W3)
        sql = cross[0].render(target_relation="gas_orgs_orgs", source_alias="segments")
        assert "gas_orgs_orgs" in sql

    def test_reference_severity_and_null_semantics(self, con) -> None:
        bundle = compile_contract(parse_contract(CONTRACT_YAML))
        r = bundle.reference_constraints("segments")[0]
        assert r.severity.value == "reject"
        sql = r.render(target_relation="stations", source_alias="segments")
        # a NULL station_id row: NOT EXISTS(... IS NOT DISTINCT FROM NULL) —
        # IS NOT DISTINCT FROM matches NULL=NULL, so NULL fk fails a present-target
        # check only when the target has no NULL; anchors semantics for W3.
        assert "IS NOT DISTINCT FROM" in sql
