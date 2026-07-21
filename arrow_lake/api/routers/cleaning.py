"""Structured data cleaning pipeline — 语义化 steps → DuckDB → 写回。

POST /api/v1/datasets/{name}/clean
  1. 读数据集 (lake.read_dataset → pyarrow Table)
  2. steps 翻译成 DuckDB SELECT 表达式 (cast/fillna/trim/lower/case/regex/split/concat/rename/drop),
     多个作用同一列的 step 链式叠加(如 cast→fillna: COALESCE(CAST(c AS DOUBLE), 0));
     filters 翻译成 WHERE
  3. 执行 DuckDB → 清洗后 Table
  4. write_back=True → lake.restore_dataset(name, table) 写回数据集

为什么用 DuckDB 而非 transforms.py(Daft):clean 端点读的是已落盘数据集
(lake.read_dataset 返回 pyarrow),DuckDB 的 arrow 互操作成熟、SQL 表达力
覆盖全部 6 类结构化变换且无 arrow↔daft 转换风险;transforms.py 留给 ingest
的 Daft 管道。

安全:SQL 片段里的列名/值/类型都来自前端请求,dtype 走白名单、regex pattern
走 re.compile + 长度校验,防 SQL 注入与 ReDoS。列名/字符串值用双/单引号转义。
"""

from __future__ import annotations

import duckdb
import pyarrow as pa
import re
from fastapi import APIRouter, Depends, Path

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.models.cleaning import (
    CleanFilter,
    CleanRequest,
    CleanResponse,
    CleanStep,
)
from arrow_lake.api.models.common import _NAME_PATTERN
from arrow_lake.api.utils import run_sync

router = APIRouter(prefix="/api/v1/datasets", tags=["cleaning"])

_CLEAN_TIMEOUT = 300
_PREVIEW_ROWS = 8

# cast 允许的目标类型白名单(防 SQL 注入:dtype 直接拼进 CAST(... AS <dt>))
_ALLOWED_DTYPES = {
    "DOUBLE", "FLOAT", "DECIMAL", "INTEGER", "BIGINT", "SMALLINT", "TINYINT",
    "VARCHAR", "TEXT", "BOOLEAN", "DATE", "TIMESTAMP",
}
_MAX_REGEX_LEN = 200


def _safe_regex_pattern(pat) -> str:
    """校验 regex pattern:非空、长度受限、re.compile 可编译(防 ReDoS / 非法模式)。"""
    pat = str(pat or "")
    if not pat or len(pat) > _MAX_REGEX_LEN:
        raise ValueError(f"regex pattern 为空或过长(>{_MAX_REGEX_LEN})")
    try:
        re.compile(pat)
    except re.error as e:
        raise ValueError(f"无效 regex pattern: {e}") from e
    return pat


# ---------------------------------------------------------------------------
# SQL 构造(语义 step → DuckDB 表达式)
# ---------------------------------------------------------------------------

def _lit(v) -> str:
    """SQL 字面量:布尔/数字直出,其余单引号字符串(转义单引号)。"""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("'", "''")
    return f"'{s}'"


def _col(name: str) -> str:
    """双引号列名(防保留字 + 基本注入防护)。"""
    return '"' + str(name).replace('"', '""') + '"'


def _transform_expr(t: str, p: dict, base: str) -> str:
    """对 base 表达式应用单列变换(cast/fillna/trim/lower/upper/regex_replace/case),返回新表达式。"""
    if t == "cast":
        dt = str(p.get("dtype") or "").upper()
        if dt not in _ALLOWED_DTYPES:
            raise ValueError(
                f"cast dtype 不在允许列表: {p.get('dtype')!r}。允许: {sorted(_ALLOWED_DTYPES)}"
            )
        return f"CAST({base} AS {dt})"
    if t == "fillna":
        return f"COALESCE({base}, {_lit(p.get('value'))})"
    if t == "trim":
        return f"trim({base})"
    if t == "lower":
        return f"lower({base})"
    if t == "upper":
        return f"upper({base})"
    if t == "regex_replace":
        pat = _safe_regex_pattern(p.get("pattern"))
        return f"regexp_replace({base}, {_lit(pat)}, {_lit(p.get('replacement', ''))})"
    if t == "case":
        mapping = p.get("mapping") or {}
        default = p.get("default")
        whens = " ".join(
            f"WHEN {base} = {_lit(k)} THEN {_lit(v)}" for k, v in mapping.items()
        )
        else_ = f"ELSE {_lit(default)}" if default is not None else "ELSE NULL"
        return f"CASE {whens} {else_} END"
    raise ValueError(f"Unsupported column transform type: {t!r}")


# 单列变换型(链式更新同列表达式)
_COL_TRANSFORMS = {"cast", "fillna", "trim", "lower", "upper", "regex_replace", "case"}


def _build_sql(steps: list[CleanStep], filters: list[CleanFilter], columns: list[str]) -> str:
    """把 steps + filters 翻译成一条 DuckDB SQL。

    - 每列维护一个「当前表达式」 (初始 = 原列名),多个作用同列的 step 链式叠加
    - split/concat 产新列;rename 改列名;drop 移除列
    """
    expr: dict[str, str] = {c: _col(c) for c in columns}
    new_cols: list[tuple[str, str]] = []  # (alias, expr)
    dropped: set[str] = set()

    for s in steps:
        t = s.type
        p = s.params
        col = s.column or p.get("column")

        if t == "drop":
            if not col:
                raise ValueError("drop requires 'column'")
            dropped.add(col)
            continue

        if t == "concat":
            cols = p.get("columns") or []
            if not cols:
                raise ValueError("concat requires params.columns")
            sep = p.get("sep", " ")
            new = p.get("as") or "_concat"
            inner = ", ".join(expr.get(c, _col(c)) for c in cols)
            new_cols.append((new, f"concat_ws({_lit(sep)}, {inner})"))
            continue

        if t == "split":
            if not col:
                raise ValueError("split requires 'column'")
            sep = p.get("sep")
            idx = p.get("index")
            if not sep or idx is None:
                raise ValueError("split requires params.sep and params.index")
            new = p.get("as") or f"{col}_{idx}"
            new_cols.append((new, f"split_part({expr[col]}, {_lit(sep)}, {int(idx)})"))
            continue

        if t == "rename":
            if not col:
                raise ValueError("rename requires 'column'")
            new = p.get("to") or p.get("as")
            if not new:
                raise ValueError("rename requires params.to")
            new_cols.append((new, expr[col]))
            dropped.add(col)
            continue

        if t in _COL_TRANSFORMS:
            if not col:
                raise ValueError(f"step '{t}' requires 'column'")
            expr[col] = _transform_expr(t, p, expr[col])
            continue

        raise ValueError(f"Unknown clean step type: {t!r}")

    parts: list[str] = []
    for c in columns:
        if c in dropped:
            continue
        parts.append(f"{expr[c]} AS {_col(c)}")
    for alias, e in new_cols:
        if alias in dropped:
            continue
        parts.append(f"{e} AS {_col(alias)}")

    sql = "SELECT " + ", ".join(parts) + " FROM t"
    if filters:
        sql += " WHERE " + " AND ".join(_filter_where(f) for f in filters)
    return sql


def _filter_where(f: CleanFilter) -> str:
    c = _col(f.column)
    if f.op == "is_null":
        return f"{c} IS NULL"
    if f.op == "is_not_null":
        return f"{c} IS NOT NULL"
    return f"{c} {f.op} {_lit(f.value)}"


def _to_pa_table(tbl) -> pa.Table:
    """把 lake.read_dataset 的返回统一成 pyarrow Table。"""
    if isinstance(tbl, pa.Table):
        return tbl
    if isinstance(tbl, list) and tbl and isinstance(tbl[0], pa.RecordBatch):
        return pa.Table.from_batches(tbl)
    for attr in ("to_arrow_table", "to_arrow", "to_pyarrow_table"):
        fn = getattr(tbl, attr, None)
        if callable(fn):
            return fn()
    if hasattr(tbl, "collect"):
        res = tbl.collect()
        for attr in ("to_arrow_table", "to_arrow", "to_pyarrow_table"):
            fn = getattr(res, attr, None)
            if callable(fn):
                return fn()
    raise TypeError(f"read_dataset 返回不可识别的类型: {type(tbl)!r}")


@router.post("/{name}/clean", response_model=CleanResponse)
async def clean_dataset(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: CleanRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> CleanResponse:
    """对结构化数据集跑清洗管道(语义 steps + filters),可选写回。

    鉴权:require_role(EDITOR) —— 与 quality 端点(dedup/filter/rules)一致,
    均为 role-level。dataset-level ACL(get_checker 框架存在)目前未应用到
    quality/clean,是项目级一致缺口,留统一加固;此处 write_back 受 EDITOR 角色约束。
    """
    table = _to_pa_table(
        await run_sync(lake.read_dataset, name, timeout=_CLEAN_TIMEOUT, label="clean_read")
    )
    con = duckdb.connect()
    con.register("t", table)
    sql = _build_sql(req.steps, req.filters, list(table.column_names))
    if req.limit:
        sql = f"SELECT * FROM ({sql}) LIMIT {int(req.limit)}"
    cleaned = con.sql(sql).to_arrow_table()

    written = False
    if req.write_back:
        await run_sync(
            lake.restore_dataset, name, cleaned, timeout=_CLEAN_TIMEOUT, label="clean_write"
        )
        written = True

    preview = cleaned.slice(0, _PREVIEW_ROWS).to_pylist()
    return CleanResponse(
        input_rows=table.num_rows,
        output_rows=cleaned.num_rows,
        columns=list(cleaned.column_names),
        written_back=written,
        preview=preview,
    )
