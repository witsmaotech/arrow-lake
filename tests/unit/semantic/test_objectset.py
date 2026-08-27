"""W4.1 (v1.11.1 F2.3) — Object Set 受限 SQL 组装。

服务端拼装(不收用户 SQL 文本):列白名单=契约∪schema、op 白名单、值按
schema 类型强转、标识符引用、对齐投影接入(W3 projection_sql)、标识/
lifecycle/外键列自动补选。422 语义:未知列/非法 op/类型不符。
"""

from __future__ import annotations

import pytest

from arrow_lake.contract.schema import parse_contract
from arrow_lake.semantic.alignment import parse_alignment
from arrow_lake.semantic.objectset import build_object_query

CONTRACT_YAML = """
dataset: gas_net
tables:
  segments:
    object_class: 管段
    lifecycle: {column: 状态, states: [在建, 在运, 报废], initial: 在建}
    identifier:
      column: seg_id
      pattern: "GAS.SEGMENT.{区域}.{序列}"
    columns:
      - {name: 压力, label: 管段运行压力, unit: kPa}
      - {name: 材质, enum: [PE, 钢管]}
  stations:
    columns: [{name: id}]
references:
  - {from: segments.station_id, to: stations.id, cardinality: N:1, kind: association}
"""

ALIGN_YAML = """
dataset: gas_net
tables:
  segments:
    columns:
      压力: {unit: {from: MPa, to: kPa}}
"""

SCHEMA_FIELDS = {
    "seg_id": "string", "压力": "double", "材质": "string",
    "状态": "string", "station_id": "string",
}


def _build(**overrides):
    kw = dict(
        contract=parse_contract(CONTRACT_YAML),
        alignment=None, table="segments", relation="gas_net.segments",
        schema_fields=SCHEMA_FIELDS, filters=(), columns=None,
        limit=50, offset=0,
    )
    kw.update(overrides)
    return build_object_query(**kw)


class TestBasicShape:
    def test_select_from_limit_offset(self) -> None:
        q = _build(limit=10, offset=5)
        assert q.sql.startswith("SELECT ")
        assert " FROM gas_net.segments " in q.sql
        assert q.sql.rstrip().endswith("LIMIT 10 OFFSET 5")
        assert q.select_columns[0] == "seg_id"  # identifier auto-first

    def test_identifier_lifecycle_fk_auto_included(self) -> None:
        q = _build(columns=["压力"])
        assert list(q.select_columns) == ["seg_id", "压力", "状态", "station_id"]

    def test_alignment_projection_applied(self) -> None:
        q = _build(columns=["压力", "材质"],
                   alignment=parse_alignment(ALIGN_YAML))
        assert '("压力" * 1000.0) AS "压力"' in q.sql
        assert q.aligned["压力"]["to"] == "kPa"
        assert '"材质"' in q.sql  # unaligned passthrough

    def test_all_schema_columns_by_default(self) -> None:
        q = _build()
        for c in SCHEMA_FIELDS:
            assert c in q.select_columns


class TestFilters:
    def test_ops_and_coercion(self) -> None:
        q = _build(filters=[
            {"column": "压力", "op": "gte", "value": "100"},
            {"column": "材质", "op": "eq", "value": "PE"},
        ])
        assert '"压力" >= 100' in q.sql
        assert '"材质" = \'PE\'' in q.sql

    def test_in_like_is_null(self) -> None:
        q = _build(filters=[
            {"column": "材质", "op": "in", "value": ["PE", "钢管"]},
            {"column": "seg_id", "op": "like", "value": "GAS%"},
            {"column": "station_id", "op": "is_null"},
        ])
        assert '"材质" IN (\'PE\', \'钢管\')' in q.sql
        assert '"seg_id" LIKE \'GAS%\'' in q.sql
        assert '"station_id" IS NULL' in q.sql

    def test_unknown_column_rejected(self) -> None:
        with pytest.raises(ValueError, match="not in schema"):
            _build(filters=[{"column": "nope", "op": "eq", "value": 1}])

    def test_unknown_op_rejected(self) -> None:
        with pytest.raises(ValueError, match="op"):
            _build(filters=[{"column": "压力", "op": "regex", "value": "x"}])

    def test_type_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="numeric"):
            _build(filters=[{"column": "压力", "op": "gt", "value": "abc"}])

    def test_in_requires_list(self) -> None:
        with pytest.raises(ValueError, match="list"):
            _build(filters=[{"column": "材质", "op": "in", "value": "PE"}])

    def test_unknown_requested_column_rejected(self) -> None:
        with pytest.raises(ValueError, match="not in schema"):
            _build(columns=["ghost"])

    def test_string_escaping(self) -> None:
        q = _build(filters=[{"column": "材质", "op": "eq", "value": "it's"}])
        assert "'it''s'" in q.sql
