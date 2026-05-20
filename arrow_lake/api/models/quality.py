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


class RuleDefinitionRequest(BaseModel):
    name: str = Field(..., min_length=1)
    column: str = Field(..., min_length=1)
    check: str = Field(..., pattern=r"^(length|range|regex|duplicate)$")
    params: dict[str, Any] = {}
    action: str = Field(default="flag", pattern=r"^(reject|flag|remove)$")
    message: str = ""


class QualityRuleSetRequest(BaseModel):
    rules: list[RuleDefinitionRequest] = Field(..., min_length=1, max_length=20)


class QualityRuleResultItem(BaseModel):
    rule_name: str
    action: str
    affected_count: int
    message: str


class QualityRuleSetResponse(BaseModel):
    success: bool = True
    applied_rules: int = 0
    results: list[QualityRuleResultItem] = []
    total_affected_rows: int = 0
