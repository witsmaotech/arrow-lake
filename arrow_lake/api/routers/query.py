"""Query endpoints: OLAP SQL, metadata SQL, Daft DataFrame."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import daft as _daft
from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_checker, get_lake, require_role
from arrow_lake.api.models.common import (
    _NAME_PATTERN,
    arrow_table_to_ipc_base64,
    arrow_table_to_response,
)
from arrow_lake.api.models.query import (
    DaftQueryRequest,
    DaftQueryResponse,
    GraphQueryRequest,
    OlapQueryRequest,
    OlapQueryResponse,
)
from arrow_lake.api.utils import run_sync
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


def _apply_pipeline(req: DaftQueryRequest, frame: LazyDaftFrame) -> LazyDaftFrame:
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
        frame = frame.sql(req.sql.query)

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
    *,
    req: OlapQueryRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
    checker=Depends(get_checker),
) -> OlapQueryResponse | StreamingResponse:
    """Execute OLAP SQL analytics query via DuckDB.

    When ``stream=True``, returns SSE events with Arrow IPC batches
    for large result sets (>10,000 rows recommended).
    """
    validate_sql_safety(req.sql)
    result = await run_sync(
        lake.olap_query, name, req.sql, max_rows=req.max_rows,
        timeout=_QUERY_TIMEOUT, label="olap_query",
    )
    table = checker.apply_table_filter(result.table, dataset=name, role=_user.role)

    if req.stream:
        return StreamingResponse(
            _stream_table(table, req.batch_size),
            media_type="text/event-stream",
            headers={"X-Row-Count": str(table.num_rows), "X-SQL": result.sql},
        )

    resp = arrow_table_to_response(table, req.format, meta={"sql": result.sql})
    return OlapQueryResponse(**resp)


@router.post("/{name}/query/metadata", response_model=OlapQueryResponse)
async def metadata_query(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: OlapQueryRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
    checker=Depends(get_checker),
) -> OlapQueryResponse:
    """Execute metadata SQL query (semantic alias for olap_query)."""
    result = await run_sync(
        lake.sql_query, name, req.sql, max_rows=req.max_rows,
        timeout=_QUERY_TIMEOUT, label="metadata_query",
    )
    table = checker.apply_table_filter(result.table, dataset=name, role=_user.role)
    resp = arrow_table_to_response(table, req.format, meta={"sql": result.sql})
    return OlapQueryResponse(**resp)


@router.post("/{name}/query/graph", response_model=OlapQueryResponse)
async def graph_query(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: GraphQueryRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> OlapQueryResponse:
    """Bounded graph traversal over the dataset's edges via recursive CTE (v1.8.0 #10).

    PGQ is unavailable in the bundled DuckDB build, so this uses a cycle-safe
    recursive CTE — complementary to HugeGraph for lightweight neighbor/path
    queries. Returns ``depth, node, path`` (+ ``cost`` when ``weight_col`` set).
    """
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
    checker=Depends(get_checker),
) -> DaftQueryResponse:
    """Load dataset via Daft, apply chained operations, return as Arrow table.

    Pre-checks row count to prevent OOM on large datasets.
    For datasets > 1M rows, use DuckDB OLAP endpoint instead.
    """
    frame = lake.daft_query(name)
    frame = _apply_pipeline(req, frame)

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
