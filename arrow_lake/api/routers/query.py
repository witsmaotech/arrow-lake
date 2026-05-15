"""Query endpoints: OLAP SQL, metadata SQL, Daft DataFrame."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

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

router = APIRouter(prefix="/api/v1/datasets", tags=["query"])

_QUERY_TIMEOUT = 300


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
    """Load dataset via Daft and return as Arrow table."""
    frame = lake.daft_query(name, columns=req.columns)
    frame = frame.limit(req.limit)
    table = await run_sync(
        frame.collect,
        timeout=_QUERY_TIMEOUT, label="daft_query",
    )
    resp = arrow_table_to_response(table, req.format)
    return DaftQueryResponse(**resp)
