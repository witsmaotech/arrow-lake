"""Quality filtering and deduplication endpoints."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Path

from arrow_lake.api.deps import get_lake
from arrow_lake.api.models.common import _NAME_PATTERN
from arrow_lake.api.models.quality import (
    DedupRequest,
    DedupResponse,
    QualityFilterRequest,
    QualityFilterResponse,
    QualityReportResponse,
)

router = APIRouter(prefix="/api/v1/datasets", tags=["quality"])


@router.post("/{name}/quality/filter", response_model=QualityFilterResponse)
async def quality_filter(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: QualityFilterRequest,
    lake=Depends(get_lake),
) -> QualityFilterResponse:
    """Run quality filters on a dataset."""
    report = lake.quality_filter(name, req.active_filters, mode=req.mode)
    return QualityFilterResponse(report=asdict(report) if hasattr(report, "__dataclass_fields__") else report)


@router.get("/{name}/quality/report", response_model=QualityReportResponse)
async def quality_report(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    lake=Depends(get_lake),
) -> QualityReportResponse:
    """Get quality report for a dataset (runs filters with default config)."""
    report = lake.quality_filter(name)
    return QualityReportResponse(report=report.to_json())


@router.post("/{name}/quality/deduplicate", response_model=DedupResponse)
async def deduplicate(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: DedupRequest,
    lake=Depends(get_lake),
) -> DedupResponse:
    """Run content deduplication on a dataset."""
    report = lake.deduplicate(
        name,
        strategy=req.strategy,
        action=req.action,
        perceptual_threshold=req.perceptual_threshold,
    )
    return DedupResponse(report=asdict(report) if hasattr(report, "__dataclass_fields__") else report)
