"""W3.2/W3.4 (v1.11.1 F2.2) — 对齐配置模型/契约软校验/投影编译。

封闭 transform 种类(S4):仅 unit(仿射,注册表或显式 factor)+ value_map,
不开放表达式。miss 透传原值(CASE ELSE);响应元数据由 projection_sql 附带。
"""

from __future__ import annotations

import duckdb
import pyarrow as pa
import pytest
from arrow_lake.contract.schema import parse_contract
from arrow_lake.semantic.alignment import (
    check_against_contract,
    parse_alignment,
    projection_sql,
)
from pydantic import ValidationError

ALIGN_YAML = """
dataset: gas_net
tables:
  measurements_src_b:
    columns:
      压力: {unit: {from: MPa, to: kPa}}
      材质: {value_map: {PE管: PE, 钢制: 钢管}}
"""

CONTRACT_YAML = """
dataset: gas_net
tables:
  measurements_src_b:
    columns:
      - {name: 压力, unit: kPa, range: [0, 4000]}
      - {name: 材质, enum: [PE, 钢管]}
"""


class TestParse:
    def test_parse_unit_and_value_map(self) -> None:
        a = parse_alignment(ALIGN_YAML)
        col = a.tables["measurements_src_b"]["压力"]
        assert col.unit is not None and col.unit.frm == "MPa" and col.unit.to == "kPa"
        vm = a.tables["measurements_src_b"]["材质"]
        assert vm.value_map == {"PE管": "PE", "钢制": "钢管"}

    def test_both_transforms_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_alignment("""
dataset: d
tables:
  t:
    columns:
      x: {unit: {from: kPa, to: MPa}, value_map: {a: b}}
""")

    def test_neither_transform_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_alignment("""
dataset: d
tables:
  t:
    columns:
      x: {}
""")

    def test_empty_value_map_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_alignment("""
dataset: d
tables:
  t:
    columns:
      x: {value_map: {}}
""")

    def test_registry_units_resolved_at_parse(self) -> None:
        # cross-dimension without explicit factor must fail at SAVE time (422)
        with pytest.raises(ValidationError):
            parse_alignment("""
dataset: d
tables:
  t:
    columns:
      x: {unit: {from: kPa, to: m}}
""")

    def test_custom_factor_skips_registry(self) -> None:
        a = parse_alignment("""
dataset: d
tables:
  t:
    columns:
      质量流量: {unit: {from: 吨/h, to: kg/h, factor: 1000}}
""")
        col = a.tables["t"]["质量流量"]
        assert col.unit is not None and col.unit.factor == 1000


class TestProjection:
    def test_unit_projection_uses_registry_factor(self) -> None:
        a = parse_alignment(ALIGN_YAML)
        expr, meta = projection_sql(a.tables["measurements_src_b"]["压力"], '"压力"')
        assert "* 1000.0" in expr
        assert meta == {"kind": "unit", "from": "MPa", "to": "kPa"}

    def test_identity_conversion_is_passthrough(self) -> None:
        a = parse_alignment("""
dataset: d
tables:
  t:
    columns:
      x: {unit: {from: kPa, to: kPa}}
""")
        expr, _ = projection_sql(a.tables["t"]["x"], '"x"')
        assert expr == '"x"'

    def test_value_map_case_with_miss_passthrough(self) -> None:
        a = parse_alignment(ALIGN_YAML)
        expr, meta = projection_sql(a.tables["measurements_src_b"]["材质"], '"材质"')
        assert expr.startswith("CASE ") and expr.endswith('ELSE "材质" END')
        assert "WHEN \"材质\" = 'PE管' THEN 'PE'" in expr
        assert meta["kind"] == "value_map"

    def test_string_escaping(self) -> None:
        a = parse_alignment("""
dataset: d
tables:
  t:
    columns:
      x: {value_map: {"it's": "ok"}}
""")
        expr, _ = projection_sql(a.tables["t"]["x"], '"x"')
        assert "'it''s'" in expr


class TestProjectionEndToEnd:
    """小样本表实测(pyarrow→DuckDB):换算/映射/miss 透传/未对齐列原样。"""

    def test_projection_over_duckdb(self) -> None:
        a = parse_alignment(ALIGN_YAML)
        cols = a.tables["measurements_src_b"]
        tbl = pa.table({
            "压力": pa.array([2.0, 0.5, None], pa.float64()),
            "材质": pa.array(["PE管", "未知材质", None], pa.string()),
            "站号": pa.array(["S1", "S2", "S3"], pa.string()),
        })
        rel = duckdb.sql("SELECT * FROM tbl")  # noqa: F841 — replacement scan resolves `rel` by local name
        proj = ", ".join(
            projection_sql(cols[c], f'"{c}"')[0] if c in cols else f'"{c}"'
            for c in tbl.column_names
        )
        rows = duckdb.sql(f"SELECT {proj} FROM rel").fetchall()
        assert [r[0] for r in rows] == [2000.0, 500.0, None]
        assert [r[1] for r in rows] == ["PE", "未知材质", None]
        assert [r[2] for r in rows] == ["S1", "S2", "S3"]


class TestContractSoftCheck:
    def test_no_warnings_when_aligned(self) -> None:
        warns = check_against_contract(parse_contract(CONTRACT_YAML), parse_alignment(ALIGN_YAML))
        assert warns == []

    def test_unit_mismatch_warns(self) -> None:
        bad = ALIGN_YAML.replace("to: kPa", "to: MPa")
        warns = check_against_contract(parse_contract(CONTRACT_YAML), parse_alignment(bad))
        assert any(w["kind"] == "unit_mismatch" for w in warns)

    def test_unknown_table_and_column_warn(self) -> None:
        contract = parse_contract("dataset: d\ntables:\n  t:\n    columns:\n      - {name: x}")
        warns = check_against_contract(
            contract,
            parse_alignment("dataset: d\ntables:\n  t:\n    columns:\n      y: {value_map: {a: b}}"),
        )
        assert any(w["kind"] == "unknown_column" for w in warns)
        warns2 = check_against_contract(
            contract,
            parse_alignment("dataset: d\ntables:\n  z:\n    columns:\n      x: {value_map: {a: b}}"),
        )
        assert any(w["kind"] == "unknown_table" for w in warns2)
