"""Data lineage tracking endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from arrow_lake.api.deps import get_lake
from arrow_lake.api.models.common import _NAME_PATTERN
from arrow_lake.api.models.lineage import (
    LineageHistoryResponse,
    LineageQueryRequest,
    LineageQueryResponse,
    LineageRecordRequest,
    LineageRecordResponse,
)

router = APIRouter(prefix="/api/v1/lineage", tags=["lineage"])

_NAME_PATTERN_Q = _NAME_PATTERN  # reuse for Query params


@router.post("/record", response_model=LineageRecordResponse)
async def lineage_record_event(
    dataset_name: str = Query(..., pattern=_NAME_PATTERN),
    *,
    req: LineageRecordRequest,
    lake=Depends(get_lake),
) -> LineageRecordResponse:
    """Record a lineage event for a dataset."""
    lake.lineage_record_event(
        dataset_name,
        req.operation,
        source_datasets=req.source_datasets,
        transform_type=req.transform_type,
        actor=req.actor,
        metadata=req.metadata,
    )
    return LineageRecordResponse(
        message=f"Lineage event recorded for dataset '{dataset_name}'"
    )


@router.get("/history/{dataset_name}", response_model=LineageHistoryResponse)
async def lineage_history(
    dataset_name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    lake=Depends(get_lake),
) -> LineageHistoryResponse:
    """Get lineage history for a dataset."""
    events = lake.lineage_history(dataset_name)
    serialized = [
        e if isinstance(e, dict) else {"event": str(e)} for e in events
    ]
    return LineageHistoryResponse(
        dataset_name=dataset_name,
        events=serialized,
    )


@router.post("/query", response_model=LineageQueryResponse)
async def lineage_query(
    *,
    req: LineageQueryRequest,
    lake=Depends(get_lake),
) -> LineageQueryResponse:
    """Query lineage events via SQL."""
    result = lake.lineage_query(req.sql)
    if hasattr(result, "to_pylist"):
        data = result.to_pylist()
    elif isinstance(result, list):
        data = result
    else:
        data = [result]
    return LineageQueryResponse(data=data)
