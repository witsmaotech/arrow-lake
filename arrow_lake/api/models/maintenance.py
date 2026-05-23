"""Pydantic models for maintenance API responses."""

from __future__ import annotations

from pydantic import BaseModel


class MaintenanceReportModel(BaseModel):
    datasets_compacted: int
    datasets_cleaned: int
    total_fragments_before: int
    total_fragments_after: int
    total_versions_removed: int
    duration_seconds: float


class MaintenanceStatusResponse(BaseModel):
    enabled: bool
    last_run: str
    next_run: str
    interval_seconds: int
    last_report: MaintenanceReportModel | None


class MaintenanceRunResponse(BaseModel):
    success: bool
    data: MaintenanceReportModel | None = None
    error: str | None = None
