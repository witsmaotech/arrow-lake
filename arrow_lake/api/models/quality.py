"""Quality filtering and deduplication request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QualityFilterRequest(BaseModel):
    active_filters: str = ""
    mode: str = Field(default="all", pattern=r"^(all|any)$")


class QualityFilterResponse(BaseModel):
    success: bool = True
    report: dict[str, Any]


class DedupRequest(BaseModel):
    strategy: str | None = None
    action: str | None = None
    perceptual_threshold: int | None = None


class DedupResponse(BaseModel):
    success: bool = True
    report: dict[str, Any]


class QualityReportResponse(BaseModel):
    success: bool = True
    report: dict[str, Any]
