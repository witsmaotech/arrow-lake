"""Query endpoints: OLAP SQL, metadata SQL, Daft DataFrame."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import daft as _daft
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import authorize_dataset_read, get_checker, get_lake, require_role
from arrow_lake.api.models.common import (
    _NAME_PATTERN,
    arrow_table_to_ipc_base64,
    arrow_table_to_response,
)


def _acl_enforced_sql(sql: str, name: str, checker: Any, role: Any) -> str:
    """Source-level row/column ACL enforcement (v1.10.7 WP1b/c, review C2).

    P0-5/P0-6 (review 2026-08-26): every table the SQL references is also
    deny-read checked via ``check_dataset_access`` — table-level deny keys
    (``ds.table``) were previously never consulted, and a pooled-session
    stale registration could not be denied at the SQL layer."""
    from arrow_lake.api.rbac_sql import AclSqlViolation, enforce_sql_acl

    try:
        return enforce_sql_acl(
            sql,
            get_acl=lambda t: checker.get_acl(t, role),
            dataset=name,
            check_read=lambda t: checker.check_dataset_access(
                role=role, dataset=t, action="read",
            ),
        )
    except AclSqlViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# P0-7 (review 2026-08-26): container tables are addressed via ``?table=``
# (the {name} path pattern forbids dots by design — do NOT relax it; a dotted
# path would bypass the container-level ACL checks that key on the container).
_TABLE_Q_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_-]{0,127}$"


def _deny_table_read(name: str, table: str | None, request: Request) -> None:
    """P0-5: when a query targets a container table, the table-level ACL
    override must actually fire — non-admin users denied read on
    ``{name}.{table}`` get 403 (layered lookup: table override first, then
    the container default)."""
    if not table:
        return
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "role", None) == Role.ADMIN:
        return
    checker = get_checker(request)
    dotted = f"{name}.{table}"
    acl = checker.get_acl(dotted, user.role if user is not None else "viewer")
    if acl is not None and "read" in (acl.denied_actions or frozenset()):
        raise HTTPException(
            status_code=403,
            detail=f"No read access to table '{dotted}' (table-level deny)",
        )
    if not checker.check_dataset_access(
        role=user.role if user is not None else "viewer", dataset=dotted, action="read",
    ):
        raise HTTPException(
            status_code=403,
            detail=f"No read access to table '{dotted}'",
        )


from arrow_lake.api.models.query import (
    DaftQueryRequest,
    DaftQueryResponse,
    GraphQueryRequest,
    MaterializeRequest,
    MaterializeResponse,
    OlapQueryRequest,
    OlapQueryResponse,
)
from arrow_lake.api.utils import olap_executor, run_sync
from arrow_lake.validation import validate_sql_safety

if TYPE_CHECKING:
    from arrow_lake.query.daft_api import LazyDaftFrame

router = APIRouter(prefix="/api/v1/datasets", tags=["query"])

_QUERY_TIMEOUT = 300


async def _stream_table(table: Any, batch_size: int = 1000) -> Any:
    """Yield SSE events, each containing a base64-encoded Arrow IPC batch."""
    import json

    from arrow_lake.query.streaming import StreamingResult

    streamer = StreamingResult(table, batch_size=batch_size)
    # First event: schema metadata
    yield f"data: {json.dumps({'type': 'schema', 'columns': table.column_names, 'row_count': table.num_rows})}\n\n"
    for batch in streamer:
        ipc_b64 = arrow_table_to_ipc_base64(batch)
        yield f"data: {json.dumps({'type': 'batch', 'rows': batch.num_rows, 'data': ipc_b64})}\n\n"
    yield f"data: {json.dumps({'type': 'done', 'total_rows': table.num_rows})}\n\n"

_FILTER_OPS = {
    "eq": lambda col, val: col == val,
    "ne": lambda col, val: col != val,
    "gt": lambda col, val: col > val,
    "gte": lambda col, val: col >= val,
    "lt": lambda col, val: col < val,
    "lte": lambda col, val: col <= val,
    "is_null": lambda col, _val: col.is_null(),
    "is_not_null": lambda col, _val: col.is_not_null(),
}

_GROUPBY_AGGS = {
    "sum": lambda g: g.sum(),
    "mean": lambda g: g.mean(),
    "count": lambda g: g.count(),
    "min": lambda g: g.min(),
    "max": lambda g: g.max(),
    "stddev": lambda g: g.stddev(),
    "var": lambda g: g.var(),
}


def _apply_pipeline(
    req: DaftQueryRequest, frame: LazyDaftFrame, *, name: str = "", checker: Any = None, role: Any = None,
) -> LazyDaftFrame:
    """Apply the chained operation pipeline from request to frame."""
    if req.sort is not None:
        frame = frame.sort(req.sort.column, desc=req.sort.desc)

    if req.filters:
        for f in req.filters:
            col_expr = _daft.col(f.column)
            predicate = _FILTER_OPS[f.op](col_expr, f.value)
            frame = frame.filter(predicate)

    if req.groupby is not None:
        grouped = frame.groupby(*req.groupby.columns)
        frame = _GROUPBY_AGGS[req.groupby.agg](grouped)

    if req.join is not None:
        raise HTTPException(status_code=501, detail="Join requires multi-dataset loading via lake")

    if req.sql is not None:
        _q = req.sql.query
        if checker is not None:
            _q = _acl_enforced_sql(_q, name, checker, role)
        frame = frame.sql(_q)

    if req.pivot is not None:
        frame = frame.pivot(
            group_by=req.pivot.group_by,
            pivot_col=req.pivot.pivot_col,
            value_col=req.pivot.value_col,
            agg_fn=req.pivot.agg_fn,
        )

    if req.explode is not None:
        frame = frame.explode(*req.explode.columns)

    if req.sample is not None:
        frame = frame.sample(
            fraction=req.sample.fraction,
            size=req.sample.size,
            seed=req.sample.seed,
        )

    if req.distinct:
        frame = frame.distinct()

    if req.columns:
        frame = frame.select(*req.columns)

    if req.offset is not None:
        frame = frame.offset(req.offset)

    frame = frame.limit(req.limit)
    return frame


@router.post("/{name}/query/olap", response_model=None)
async def olap_query(
    name: str = Path(..., pattern=_NAME_PATTERN),
    table: str | None = Query(None, pattern=_TABLE_Q_PATTERN),
    *,
    req: OlapQueryRequest,
    request: Request,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
    _acl_guard: None = Depends(authorize_dataset_read),
    checker=Depends(get_checker),
) -> OlapQueryResponse | StreamingResponse:
    """Execute OLAP SQL analytics query via DuckDB.

    When ``stream=True``, returns SSE events with Arrow IPC batches
    for large result sets (>10,000 rows recommended).

    ``?table=`` addresses a table inside a container dataset (P0-7): the
    two-part target ``{name}.{table}`` is what gets registered, so SQL can
    reference ``FROM {name}.{table}``; table-level deny-read is enforced
    before execution (P0-5).
    """
    _deny_table_read(name, table, request)
    target = f"{name}.{table}" if table else name
    try:
        validate_sql_safety(req.sql)
    except ValueError as exc:
        # 校验拒绝是可读的 422,不是 500(console 预览/Worksheet 的预期文案)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    sql = _acl_enforced_sql(req.sql, target, checker, _user.role)
    result = await run_sync(
        lake.olap_query, target, sql, max_rows=req.max_rows,
        timeout=_QUERY_TIMEOUT, label="olap_query", executor=olap_executor,
    )
    table_ = checker.apply_table_filter(result.table, dataset=target, role=_user.role)

    if req.stream:
        # M-9 (review 2026-08-24): X-SQL 回显用户原始 SQL,enforced 版本含
        # 行过滤谓词值(admin 配置的 PII 范围),受限用户不可见
        return StreamingResponse(
            _stream_table(table_, req.batch_size),
            media_type="text/event-stream",
            headers={"X-Row-Count": str(table_.num_rows), "X-SQL": req.sql},
        )

    # M-9: meta.sql 同理——回显原始,不泄 enforced 形态
    resp = arrow_table_to_response(table_, req.format, meta={"sql": req.sql})
    return OlapQueryResponse(**resp)


@router.post("/{name}/query/metadata", response_model=OlapQueryResponse)
async def metadata_query(
    name: str = Path(..., pattern=_NAME_PATTERN),
    table: str | None = Query(None, pattern=_TABLE_Q_PATTERN),
    *,
    req: OlapQueryRequest,
    request: Request,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
    _acl_guard: None = Depends(authorize_dataset_read),
    checker=Depends(get_checker),
) -> OlapQueryResponse:
    """Execute metadata SQL query (semantic alias for olap_query).

    ``?table=`` addresses a container table (P0-7), with table-level
    deny-read enforced first (P0-5).
    """
    _deny_table_read(name, table, request)
    target = f"{name}.{table}" if table else name
    try:
        validate_sql_safety(req.sql)
    except ValueError as exc:
        # 校验拒绝是可读的 422,不是 500(console 预览/Worksheet 的预期文案)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    sql = _acl_enforced_sql(req.sql, target, checker, _user.role)
    result = await run_sync(
        lake.sql_query, target, sql, max_rows=req.max_rows,
        timeout=_QUERY_TIMEOUT, label="metadata_query", executor=olap_executor,
    )
    table_ = checker.apply_table_filter(result.table, dataset=target, role=_user.role)
    # M-9: meta.sql 回显原始 SQL,enforced 版本含行过滤谓词值
    resp = arrow_table_to_response(table_, req.format, meta={"sql": req.sql})
    return OlapQueryResponse(**resp)


@router.post("/{name}/query/graph", response_model=OlapQueryResponse)
async def graph_query(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: GraphQueryRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    _acl_guard: None = Depends(authorize_dataset_read),
    checker=Depends(get_checker),
) -> OlapQueryResponse:
    """Bounded graph traversal over the dataset's edges via recursive CTE (v1.8.0 #10).

    PGQ is unavailable in the bundled DuckDB build, so this uses a cycle-safe
    recursive CTE — complementary to HugeGraph for lightweight neighbor/path
    queries. Returns ``depth, node, path`` (+ ``cost`` when ``weight_col`` set).
    """
    # M-10 (review 2026-08-24): src/dst/weight 列须在用户 visible_columns
    # 内——隐藏列值可通过遍历结果被推理(node/path 回显真实值)
    acl = checker.get_acl(name, _user.role)
    visible = getattr(acl, "visible_columns", None) if acl is not None else None
    if visible:
        allowed = set(visible)
        for col in (req.src_col, req.dst_col, req.weight_col):
            if col and col not in allowed:
                raise HTTPException(
                    status_code=422,
                    detail=f"column '{col}' not in your visible columns",
                )
    result = await run_sync(
        lake.graph_query,
        name,
        src_col=req.src_col,
        dst_col=req.dst_col,
        start_node=req.start_node,
        max_depth=req.max_depth,
        weight_col=req.weight_col,
        directed=req.directed,
        timeout=_QUERY_TIMEOUT,
        label="graph_query",
        executor=olap_executor,
    )
    table = checker.apply_table_filter(result.table, dataset=name, role=_user.role)
    resp = arrow_table_to_response(table, req.format, meta={"sql": result.sql})
    return OlapQueryResponse(**resp)


@router.post("/{name}/query/daft", response_model=DaftQueryResponse)
async def daft_query(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: DaftQueryRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    _acl_guard: None = Depends(authorize_dataset_read),
    checker=Depends(get_checker),
) -> DaftQueryResponse:
    """Load dataset via Daft, apply chained operations, return as Arrow table.

    Pre-checks row count to prevent OOM on large datasets.
    For datasets > 1M rows, use DuckDB OLAP endpoint instead.
    """
    frame = lake.daft_query(name)
    frame = _apply_pipeline(req, frame, name=name, checker=checker, role=_user.role)

    try:
        warnings = await run_sync(
            frame.check_feasibility,
            timeout=30, label="daft_row_check",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    max_rows = req.max_rows if req.max_rows and req.max_rows > 0 else None
    collect_kwargs: dict = {}
    if max_rows is not None:
        collect_kwargs["max_rows"] = max_rows
    table = await run_sync(
        frame.collect,
        timeout=_QUERY_TIMEOUT, label="daft_query",
        **collect_kwargs,
    )
    table = checker.apply_table_filter(table, dataset=name, role=_user.role)
    resp = arrow_table_to_response(table, req.format)
    resp["warnings"] = warnings
    return DaftQueryResponse(**resp)


@router.post("/{name}/materialize", response_model=MaterializeResponse)
async def materialize_view(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: MaterializeRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> MaterializeResponse:
    """Materialize a SELECT result as a DuckLake table (ADMIN).

    Internal ``CREATE TABLE`` bypasses user-SQL screening, so the view name is
    whitelist-validated (model pattern) and the endpoint is ADMIN-gated.
    Returns 503 when ``ducklake_enabled=False``.
    """
    from datetime import UTC, datetime

    from arrow_lake.exceptions import ArrowLakeError, QueryError

    ttl_days = max(1, round(req.ttl_hours / 24)) if req.ttl_hours is not None else None
    try:
        row_count = await run_sync(
            lake.materialize, name, req.sql,
            view_name=req.view_name, ttl_days=ttl_days,
            timeout=_QUERY_TIMEOUT, label="materialize",
        )
    except (QueryError, ArrowLakeError) as exc:
        msg = str(exc)
        raise HTTPException(
            status_code=503 if "not enabled" in msg else 400, detail=msg,
        ) from exc
    return MaterializeResponse(
        view_name=req.view_name,
        row_count=row_count,
        materialized_at=datetime.now(UTC).isoformat(),
    )
