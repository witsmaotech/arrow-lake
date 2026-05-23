"""Storage maintenance endpoints — status and manual trigger."""

from __future__ import annotations

from fastapi import APIRouter, Request

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import require_role
from arrow_lake.api.models.maintenance import (
    MaintenanceReportModel,
    MaintenanceRunResponse,
    MaintenanceStatusResponse,
)

router = APIRouter(prefix="/api/v1/admin/maintenance", tags=["admin"])


def _get_scheduler(request: Request) -> object | None:
    return getattr(request.app.state, "maintenance_scheduler", None)


@router.get("/status", response_model=MaintenanceStatusResponse)
async def maintenance_status(
    request: Request,
    _user: dict = require_role(Role.ADMIN),
) -> MaintenanceStatusResponse:
    """Get current maintenance scheduler status."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return MaintenanceStatusResponse(
            enabled=False, last_run="", next_run="", interval_seconds=0, last_report=None
        )
    st = scheduler.status()
    report = None
    if st.last_report is not None:
        report = MaintenanceReportModel(
            datasets_compacted=st.last_report.datasets_compacted,
            datasets_cleaned=st.last_report.datasets_cleaned,
            total_fragments_before=st.last_report.total_fragments_before,
            total_fragments_after=st.last_report.total_fragments_after,
            total_versions_removed=st.last_report.total_versions_removed,
            duration_seconds=st.last_report.duration_seconds,
        )
    return MaintenanceStatusResponse(
        enabled=st.enabled,
        last_run=st.last_run,
        next_run=st.next_run,
        interval_seconds=st.interval_seconds,
        last_report=report,
    )


@router.post("/run", response_model=MaintenanceRunResponse)
async def maintenance_run(
    request: Request,
    _user: dict = require_role(Role.ADMIN),
) -> MaintenanceRunResponse:
    """Manually trigger a maintenance cycle."""
    scheduler = _get_scheduler(request)
    if scheduler is None:
        return MaintenanceRunResponse(success=False, error="Maintenance scheduler is not enabled")
    try:
        report = scheduler.run_once()
        data = MaintenanceReportModel(
            datasets_compacted=report.datasets_compacted,
            datasets_cleaned=report.datasets_cleaned,
            total_fragments_before=report.total_fragments_before,
            total_fragments_after=report.total_fragments_after,
            total_versions_removed=report.total_versions_removed,
            duration_seconds=report.duration_seconds,
        )
        return MaintenanceRunResponse(success=True, data=data)
    except Exception as exc:
        return MaintenanceRunResponse(success=False, error=str(exc))
