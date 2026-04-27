"""Query endpoints: OLAP SQL, metadata SQL, Daft DataFrame."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Path

from arrow_lake.api.deps import get_lake
from arrow_lake.api.models.common import _NAME_PATTERN, arrow_table_to_response
from arrow_lake.api.models.query import (
    DaftQueryRequest,
    DaftQueryResponse,
    OlapQueryRequest,
    OlapQueryResponse,
)

router = APIRouter(prefix="/api/v1/datasets", tags=["query"])

_QUERY_TIMEOUT = 300


@router.post("/{name}/query/olap", response_model=OlapQueryResponse)
async def olap_query(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: OlapQueryRequest,
    lake=Depends(get_lake),
) -> OlapQueryResponse:
    """Execute OLAP SQL analytics query via DuckDB."""
    result = await asyncio.wait_for(
        asyncio.get_running_loop().run_in_executor(
            None, lambda: lake.olap_query(name, req.sql, max_rows=req.max_rows),
        ),
        timeout=_QUERY_TIMEOUT,
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
) -> OlapQueryResponse:
    """Execute metadata SQL query (semantic alias for olap_query)."""
    result = await asyncio.wait_for(
        asyncio.get_running_loop().run_in_executor(
            None, lambda: lake.sql_query(name, req.sql, max_rows=req.max_rows),
        ),
        timeout=_QUERY_TIMEOUT,
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
) -> DaftQueryResponse:
    """Load dataset via Daft and return as Arrow table."""
    frame = lake.daft_query(name, columns=req.columns)
    table = await asyncio.wait_for(
        asyncio.get_running_loop().run_in_executor(None, frame.collect),
        timeout=_QUERY_TIMEOUT,
    )
    resp = arrow_table_to_response(table, req.format)
    return DaftQueryResponse(**resp)
