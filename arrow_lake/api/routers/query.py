"""Query endpoints: OLAP SQL, metadata SQL, Daft DataFrame."""

from __future__ import annotations

from typing import TYPE_CHECKING

import daft as _daft
from fastapi import APIRouter, Depends, HTTPException, Path

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.models.common import _NAME_PATTERN, arrow_table_to_response
from arrow_lake.api.models.query import (
    DaftQueryRequest,
    DaftQueryResponse,
    OlapQueryRequest,
    OlapQueryResponse,
)
from arrow_lake.api.utils import run_sync

if TYPE_CHECKING:
    from arrow_lake.query.daft_api import LazyDaftFrame

router = APIRouter(prefix="/api/v1/datasets", tags=["query"])

_QUERY_TIMEOUT = 300

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


@router.post("/{name}/query/olap", response_model=OlapQueryResponse)
async def olap_query(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: OlapQueryRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> OlapQueryResponse:
    """Execute OLAP SQL analytics query via DuckDB."""
    result = await run_sync(
        lake.olap_query, name, req.sql, max_rows=req.max_rows,
        timeout=_QUERY_TIMEOUT, label="olap_query",
    )
    resp = arrow_table_to_response(
        result.table,
        req.format,
        meta={"sql": result.sql},
    )
    return OlapQueryResponse(**resp)


@router.post("/{name}/query/metadata", response_model=OlapQueryResponse)
async def metadata_query(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: OlapQueryRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> OlapQueryResponse:
    """Execute metadata SQL query (semantic alias for olap_query)."""
    result = await run_sync(
        lake.sql_query, name, req.sql, max_rows=req.max_rows,
        timeout=_QUERY_TIMEOUT, label="metadata_query",
    )
    resp = arrow_table_to_response(
        result.table,
        req.format,
        meta={"sql": result.sql},
    )
    return OlapQueryResponse(**resp)


@router.post("/{name}/query/daft", response_model=DaftQueryResponse)
async def daft_query(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: DaftQueryRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> DaftQueryResponse:
    """Load dataset via Daft, apply chained operations, return as Arrow table."""
    frame = lake.daft_query(name)
    frame = _apply_pipeline(req, frame)
    max_rows = req.max_rows if req.max_rows and req.max_rows > 0 else None
    collect_kwargs: dict = {}
    if max_rows is not None:
        collect_kwargs["max_rows"] = max_rows
    table = await run_sync(
        frame.collect,
        timeout=_QUERY_TIMEOUT, label="daft_query",
        **collect_kwargs,
    )
    resp = arrow_table_to_response(table, req.format)
    return DaftQueryResponse(**resp)
