"""Data lineage tracking endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.models.common import _NAME_PATTERN
from arrow_lake.api.models.lineage import (
    LineageEdge,
    LineageGraphResponse,
    LineageGraphStats,
    LineageHistoryResponse,
    LineageImpactItem,
    LineageImpactRequest,
    LineageImpactResponse,
    LineageNode,
    LineageQueryRequest,
    LineageQueryResponse,
    LineageRecordRequest,
    LineageRecordResponse,
    LineageStatsResponse,
)
from arrow_lake.api.utils import run_sync

router = APIRouter(prefix="/api/v1/lineage", tags=["lineage"])

_LINEAGE_TIMEOUT = 60


@router.post("/record", response_model=LineageRecordResponse)
async def lineage_record_event(
    dataset_name: str = Query(..., pattern=_NAME_PATTERN),
    *,
    req: LineageRecordRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> LineageRecordResponse:
    """Record a lineage event for a dataset."""
    await run_sync(
        lake.lineage_record_event,
        dataset_name, req.operation,
        source_datasets=req.source_datasets,
        transform_type=req.transform_type,
        actor=req.actor, metadata=req.metadata,
        timeout=_LINEAGE_TIMEOUT, label="lineage_record",
    )
    return LineageRecordResponse(
        message=f"Lineage event recorded for dataset '{dataset_name}'"
    )


@router.get("/history/{dataset_name}", response_model=LineageHistoryResponse)
async def lineage_history(
    dataset_name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> LineageHistoryResponse:
    """Get lineage history for a dataset."""
    events = await run_sync(
        lake.lineage_history, dataset_name,
        timeout=_LINEAGE_TIMEOUT, label="lineage_history",
    )
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
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> LineageQueryResponse:
    """Query lineage events via SQL."""
    result = await run_sync(
        lake.lineage_query, req.sql,
        timeout=_LINEAGE_TIMEOUT, label="lineage_query",
    )
    if hasattr(result, "to_pylist"):
        data = result.to_pylist()
    elif isinstance(result, list):
        data = result
    else:
        data = [result]
    return LineageQueryResponse(data=data)


@router.get("/graph/{dataset_name}", response_model=LineageGraphResponse)
async def lineage_graph(
    dataset_name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    max_depth: int = Query(default=10, ge=1, le=20),
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> LineageGraphResponse:
    """Get the full lineage graph for a dataset (upstream + downstream)."""
    result = await run_sync(
        lake.lineage_graph, dataset_name,
        max_depth=max_depth,
        timeout=_LINEAGE_TIMEOUT, label="lineage_graph",
    )
    return LineageGraphResponse(
        dataset_name=dataset_name,
        nodes=[LineageNode(**n) for n in result.get("nodes", [])],
        edges=[
            LineageEdge(**{("from_" if k == "from" else k): v for k, v in e.items()})
            for e in result.get("edges", [])
        ],
        stats=LineageGraphStats(**result.get("stats", {})),
    )


@router.post("/impact", response_model=LineageImpactResponse)
async def lineage_impact(
    *,
    req: LineageImpactRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> LineageImpactResponse:
    """Analyze downstream impact of changing a dataset."""
    result = await run_sync(
        lake.lineage_impact, req.dataset_name,
        timeout=_LINEAGE_TIMEOUT, label="lineage_impact",
    )
    return LineageImpactResponse(
        source_dataset=req.dataset_name,
        impacted_datasets=[LineageImpactItem(**item) for item in result],
    )


@router.get("/stats", response_model=LineageStatsResponse)
async def lineage_stats(
    *,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> LineageStatsResponse:
    """Get lineage tracking statistics."""
    history = await run_sync(
        lake.lineage_history, "__all__",
        timeout=_LINEAGE_TIMEOUT, label="lineage_stats",
    )
    datasets = set()
    total_events = 0
    if isinstance(history, list):
        total_events = len(history)
        for event in history:
            if isinstance(event, dict):
                ds = event.get("dataset_name")
                if ds:
                    datasets.add(ds)
            else:
                ds = getattr(event, "dataset_name", None)
                if ds:
                    datasets.add(ds)
    return LineageStatsResponse(
        total_datasets_tracked=len(datasets),
        total_events=total_events,
    )
