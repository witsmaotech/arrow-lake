"""Object Set 受限 SQL 组装(v1.11.1 W4.1,F2.3)。

服务端拼装,**不收用户 SQL 文本**:列白名单=schema(契约标注 enrich)、
op 白名单、值按 schema 类型强转、标识符引用(列名可中文)、对齐投影接入
(W3 ``projection_sql``)。标识/lifecycle/外键列自动补选——路由层聚合
(object_id/lifecycle_state/_links)依赖它们出现在结果里。

产出的 SQL 随后走 OLAP 同一条安全路径(``validate_sql_safety`` →
``enforce_sql_acl`` → 执行),权限语义与 /query/olap 完全一致(W4.2)。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from arrow_lake.contract.schema import DatasetContract
from arrow_lake.semantic.alignment import SemanticAlignment, projection_sql

_OPS = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_NULL_OPS = {"is_null", "is_not_null"}
_LIST_OPS = {"in"}
_NUMERIC_HINTS = ("int", "float", "double", "decimal")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(v: Any) -> str:
    return "'" + str(v).replace("'", "''") + "'"


@dataclass(frozen=True)
class ObjectSetSql:
    sql: str
    select_columns: tuple[str, ...]
    aligned: dict[str, dict[str, Any]]


def _coerce_literal(column: str, type_str: str, value: Any) -> str:
    """值 → SQL 字面量,按 schema 类型强转(不符 → ValueError,422)。"""
    t = (type_str or "").lower()
    if any(h in t for h in _NUMERIC_HINTS):
        try:
            f = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"filter value for numeric column '{column}' must be numeric, "
                f"got {value!r}"
            ) from exc
        return repr(int(f)) if f.is_integer() and "float" not in t and \
            "double" not in t else repr(f)
    if t.startswith("bool"):
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        raise ValueError(f"filter value for bool column '{column}' must be a boolean")
    if not isinstance(value, str):
        value = str(value)
    if not value:
        raise ValueError(f"filter value for column '{column}' must be non-empty")
    return _lit(value)


def build_object_query(
    *,
    contract: DatasetContract,
    alignment: SemanticAlignment | None,
    table: str,
    relation: str,
    schema_fields: Mapping[str, str],
    filters: Sequence[Mapping[str, Any]] = (),
    columns: Sequence[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> ObjectSetSql:
    """组装一次对象查询的受限 SQL。

    ``relation`` 是调用方定界的 FROM 目标(容器二段名 ``ds.table`` 或
    单表裸名——与 /query/olap 的 target 注册形态一致)。
    """
    section = contract.tables.get(table)
    if section is None:
        raise ValueError(f"object type '{table}' not in contract")

    table_align: Mapping[str, Any] = (
        alignment.tables.get(table, {}) if alignment is not None else {}
    )

    # 聚合依赖列自动补选:identifier → lifecycle → 本表外键(_links)。
    auto: list[str] = []
    if section.identifier is not None and section.identifier.column in schema_fields:
        auto.append(section.identifier.column)
    lc = (section.lifecycle.column
          if section.lifecycle is not None and section.lifecycle.column else None)
    if lc is not None and lc in schema_fields and lc not in auto:
        auto.append(lc)
    for ref in contract.references:
        if (ref.from_table == table and ref.from_column in schema_fields
                and ref.from_column not in auto):
            auto.append(ref.from_column)

    if columns is None:
        requested = list(schema_fields.keys())
    else:
        for c in columns:
            if c not in schema_fields:
                raise ValueError(f"requested column '{c}' not in schema")
        requested = list(columns)
    # identifier 在首位(对象身份),其余聚合依赖列(lifecycle/外键)排尾部
    head = auto[:1]
    tail = auto[1:]
    ordered = list(dict.fromkeys(head + requested + tail))

    proj_parts: list[str] = []
    aligned_meta: dict[str, dict[str, Any]] = {}
    for c in ordered:
        q = _q(c)
        ca = table_align.get(c)
        if ca is not None:
            expr, meta = projection_sql(ca, q)
            aligned_meta[c] = meta
            proj_parts.append(f"{expr} AS {q}" if expr != q else q)
        else:
            proj_parts.append(q)

    where: list[str] = []
    for f in filters:
        col, op = f.get("column"), f.get("op")
        if col not in schema_fields:
            raise ValueError(f"filter column '{col}' not in schema")
        if op in _NULL_OPS:
            where.append(f"{_q(col)} IS NULL" if op == "is_null"
                         else f"{_q(col)} IS NOT NULL")
            continue
        if op == "like":
            value = _coerce_literal(col, schema_fields[col], f.get("value"))
            where.append(f"{_q(col)} LIKE {value}")
            continue
        if op in _LIST_OPS:
            values = f.get("value")
            if not isinstance(values, (list, tuple)):
                raise ValueError(f"filter op 'in' on '{col}' needs a list value")
            lits = [_coerce_literal(col, schema_fields[col], v) for v in values]
            where.append(f"{_q(col)} IN ({', '.join(lits)})")
            continue
        if op not in _OPS:
            raise ValueError(f"unsupported filter op '{op}'")
        lit = _coerce_literal(col, schema_fields[col], f.get("value"))
        where.append(f"{_q(col)} {_OPS[op]} {lit}")

    sql = f"SELECT {', '.join(proj_parts)} FROM {relation}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    return ObjectSetSql(sql=sql, select_columns=tuple(ordered), aligned=aligned_meta)
