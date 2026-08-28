"""Object Set 受限 SQL 组装(v1.11.1 W4.1,F2.3)+ 运行时取数管线
(v1.11.2 W3.1)。

服务端拼装,**不收用户 SQL 文本**:列白名单=schema(契约标注 enrich)、
op 白名单、值按 schema 类型强转、标识符引用(列名可中文)、对齐投影接入
(W3 ``projection_sql``)。标识/lifecycle/外键列自动补选——路由层聚合
(object_id/lifecycle_state/_links)依赖它们出现在结果里。

产出的 SQL 随后走 OLAP 同一条安全路径(``validate_sql_safety`` →
``enforce_sql_acl`` → 执行),权限语义与 /query/olap 完全一致(W4.2)。

W3.1:objects 端点内的编排段(读权守卫→契约→物理寻址→表级 deny→
schema→对齐→组装→安全→ACL→执行→表过滤)提取为 :func:`fetch_object_rows`
——objects 端点与 decisions/assess 共用**同一条取数+ACL 管线**(S6:
研判输入=对齐后口径,不建旁路)。deny/ACL 两步经调用方闭包注入,确保与
/query/olap 路径字面同源。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from arrow_lake.contract.schema import DatasetContract, parse_contract
from arrow_lake.semantic.alignment import SemanticAlignment, parse_alignment, projection_sql

logger = logging.getLogger(__name__)

_OPS = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_NULL_OPS = {"is_null", "is_not_null"}
_LIST_OPS = {"in"}
_NUMERIC_HINTS = ("int", "float", "double", "decimal")
_STRING_HINTS = ("string", "varchar", "char", "utf8")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _q_relation(relation: str) -> str:
    """Quote a (possibly two-part) FROM target — hyphen/uppercase/digit-led
    legal names parse correctly (review F15; injection stays closed by the
    contract-save whitelist + validate_sql_safety structure checks)."""
    return ".".join(_q(p) for p in relation.split("."))


def _lit(v: Any) -> str:
    return "'" + str(v).replace("'", "''") + "'"


@dataclass(frozen=True)
class ObjectSetSql:
    sql: str
    select_columns: tuple[str, ...]
    aligned: dict[str, dict[str, Any]]


def _coerce_literal(column: str, type_str: str, value: Any) -> str:
    """值 → SQL 字面量,按 schema 类型强转(不符 → ValueError,422)。

    review 加固:int 值直达 repr 不经 float()(int64 雪花 ID 保精度,F6);
    inf/nan 拒(F10);None 显式拒(F10——字符串路径曾 str(None) 成 'None')。
    """
    if value is None:
        raise ValueError(f"filter value for column '{column}' must not be null (use is_null)")
    t = (type_str or "").lower()
    if any(h in t for h in _NUMERIC_HINTS):
        if isinstance(value, bool):
            raise ValueError(
                f"filter value for numeric column '{column}' must be numeric, got {value!r}"
            )
        if isinstance(value, int) and "float" not in t and "double" not in t and "decimal" not in t:
            return repr(value)  # exact int64 — no float() round-trip
        try:
            f = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"filter value for numeric column '{column}' must be numeric, got {value!r}"
            ) from exc
        if not math.isfinite(f):
            raise ValueError(
                f"filter value for numeric column '{column}' must be finite (inf/nan rejected)"
            )
        return (
            repr(int(f)) if f.is_integer() and "float" not in t and "double" not in t else repr(f)
        )
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
    id_column: str | None = None,
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

    # 聚合依赖列自动补选:identifier → id_column(无声明时,F9)→ lifecycle
    # → 本表外键(_links)。
    auto: list[str] = []
    if section.identifier is not None and section.identifier.column in schema_fields:
        auto.append(section.identifier.column)
    if id_column is not None:
        if id_column not in schema_fields:
            raise ValueError(f"id_column '{id_column}' not in schema")
        if id_column not in auto:
            auto.append(id_column)
    lc = (
        section.lifecycle.column
        if section.lifecycle is not None and section.lifecycle.column
        else None
    )
    if lc is not None and lc in schema_fields and lc not in auto:
        auto.append(lc)
    for ref in contract.references:
        if (
            ref.from_table == table
            and ref.from_column in schema_fields
            and ref.from_column not in auto
        ):
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
            where.append(f"{_q(col)} IS NULL" if op == "is_null" else f"{_q(col)} IS NOT NULL")
            continue
        if op == "like":
            # F7: LIKE on numeric columns is a DuckDB binder error at RUNTIME
            # (500) — reject at composition time (422). String-ish columns only.
            ft = (schema_fields[col] or "").lower()
            if not any(h in ft for h in _STRING_HINTS):
                raise ValueError(
                    f"filter op 'like' only applies to string columns "
                    f"('{col}' is {schema_fields[col]})"
                )
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

    sql = f"SELECT {', '.join(proj_parts)} FROM {_q_relation(relation)}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    return ObjectSetSql(sql=sql, select_columns=tuple(ordered), aligned=aligned_meta)


# ---------------------------------------------------------------------------
# W3.1 运行时取数管线(v1.11.2):objects 端点与 decisions/assess 共用。
# 从 routers/objects.py 逐行提取,语义零改动;deny/ACL 两步经闭包注入
# (调用方绑定 _deny_table_read(request)/_acl_enforced_sql(checker,role)),
# 与 /query/olap 的安全路径字面同源——不建旁路。
# ---------------------------------------------------------------------------

_RUNTIME_TIMEOUT = 60


@dataclass(frozen=True)
class ObjectSetRows:
    """一次对象取数的结果(对齐后口径)。

    ``rows`` 是 apply_table_filter 之后的每行 dict(对齐投影已生效);
    ``sql`` 为 PRE-enforcement 形态(回显语义沿 objects F10)。
    """

    rows: list[dict[str, Any]]
    result_columns: tuple[str, ...]
    contract: DatasetContract
    object_type: str
    ident_col: str | None
    lifecycle_col: str | None
    target: str
    aligned: dict[str, dict[str, Any]]
    sql: str


async def fetch_object_rows(
    *,
    lake: Any,
    checker: Any,
    role: Any,
    permissions: Any,
    dataset: str,
    object_type: str,
    filters: Sequence[Mapping[str, Any]] = (),
    columns: Sequence[str] | None = None,
    id_column: str | None = None,
    object_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    contract_store: Any,
    alignment_store: Any | None = None,
    deny_table_read: Callable[[str, str | None], None],
    acl_enforce: Callable[[str, str], str],
) -> ObjectSetRows:
    """受限取数:读权守卫 → 契约(S8)→ 物理寻址 → 表级 deny → schema →
    对齐 → build_object_query → validate_sql_safety → ACL → OLAP 执行 →
    apply_table_filter → 行列表。

    ``object_id`` 给定时追加标识列 eq 过滤(取单对象;ident_col 缺位 →
    422,F9 语义)。错误码/明细与原 objects 端点逐字一致(零回归验收)。
    """
    from arrow_lake.api.utils import olap_executor, run_sync  # function-level:避免层间 import 环
    from arrow_lake.validation import validate_sql_safety

    if contract_store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; contracts unavailable")

    # -- 安全关键:dataset 级读权(镜像 kg 路由的检查;F3: scoped tokens) --
    if not checker.check_dataset_access(
        role=role, dataset=dataset, action="read", permissions=permissions
    ):
        raise HTTPException(status_code=403, detail=f"Read access to dataset '{dataset}' denied")

    latest = contract_store.get_version(dataset)
    if latest is None:
        raise HTTPException(
            status_code=422,
            detail=f"Dataset '{dataset}' has no contract — the contract "
            f"is the precondition of the object layer (S8)",
        )
    try:
        contract = parse_contract(latest["contract_yaml"])
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid contract: {exc}") from exc
    if object_type not in contract.tables:
        raise HTTPException(
            status_code=422,
            detail=f"object_type '{object_type}' not in contract "
            f"(known: {', '.join(sorted(contract.tables))})",
        )
    section = contract.tables[object_type]

    # -- 物理寻址:容器二段名 / 单表裸名(与 /query/olap 同形态) ---------
    def _probe() -> list[str]:
        got = lake._get_storage().list_container_tables(dataset)
        return list(got) if isinstance(got, (list, tuple)) else []

    container_tables = await run_sync(
        _probe, timeout=_RUNTIME_TIMEOUT, label="objects_container_probe"
    )
    if container_tables:
        if object_type not in container_tables:
            raise HTTPException(
                status_code=422,
                detail=f"object_type '{object_type}' not a physical table "
                f"(available: {', '.join(sorted(container_tables))})",
            )
        table_param: str | None = object_type
        target = f"{dataset}.{object_type}"
    else:
        table_param = None
        target = dataset

    # 表级 deny 双查(P0-5 同款;ADMIN 豁免由其内部处理)
    deny_table_read(dataset, table_param)

    def _schema() -> Any:
        return lake.open_dataset(dataset, table=table_param).schema

    schema = await run_sync(_schema, timeout=_RUNTIME_TIMEOUT, label="objects_schema")
    schema_fields = {f.name: str(f.type) for f in schema}
    if id_column is not None and id_column not in schema_fields:
        raise HTTPException(
            status_code=422,
            detail=f"id_column '{id_column}' not in schema (F9: reject "
            f"instead of silently losing identity)",
        )

    ident_col = section.identifier.column if section.identifier else id_column
    if object_id is not None:
        if ident_col is None:
            raise HTTPException(
                status_code=422,
                detail=f"object_type '{object_type}' has no identifier column "
                f"(contract identifier or id_column required to fetch by "
                f"object_id)",
            )
        filters = [*filters, {"column": ident_col, "op": "eq", "value": object_id}]

    alignment: SemanticAlignment | None = None
    if alignment_store is not None:
        arec = alignment_store.get_version(dataset)
        if arec is not None:
            try:
                alignment = parse_alignment(arec["alignment_yaml"])
            except Exception:  # 对齐配置腐烂不阻塞查询(原样返回)
                logger.warning("objects_alignment_parse_failed", exc_info=True)

    try:
        built = build_object_query(
            contract=contract,
            alignment=alignment,
            table=object_type,
            relation=target,
            schema_fields=schema_fields,
            filters=list(filters),
            columns=columns,
            id_column=id_column,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        validate_sql_safety(built.sql)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    sql = acl_enforce(built.sql, target)

    result = await run_sync(
        lake.olap_query,
        target,
        sql,
        max_rows=limit,
        timeout=300,
        label="objectset_query",
        executor=olap_executor,
    )
    table_ = checker.apply_table_filter(result.table, dataset=target, role=role)

    return ObjectSetRows(
        rows=table_.to_pylist(),
        result_columns=tuple(table_.column_names),
        contract=contract,
        object_type=object_type,
        ident_col=ident_col,
        lifecycle_col=(section.lifecycle.column if section.lifecycle is not None else None),
        target=target,
        aligned=dict(built.aligned),
        sql=built.sql,
    )
