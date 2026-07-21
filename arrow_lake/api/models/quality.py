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
    text_column: str | None = None


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


# ---------------------------------------------------------------------------
# Data-prep enrichment (LLM labeling & structured extraction) — async tasks
# ---------------------------------------------------------------------------


class LlmLabelRequest(BaseModel):
    """Batch-LLM labeling: render ``prompt_template`` per row, write a new column."""

    column: str
    new_column: str
    prompt_template: str  # must contain {text}
    model: str | None = None
    max_rows: int | None = Field(default=None, ge=1, le=10000)
    concurrency: int = Field(default=8, ge=1, le=32)


class ExtractFieldDef(BaseModel):
    """One field to extract from text (stored as string)."""

    name: str
    type: str = "string"
    description: str = ""


class ExtractRequest(BaseModel):
    """Batch structured extraction: text column → multiple new columns."""

    column: str
    fields: list[ExtractFieldDef] = Field(..., min_length=1, max_length=20)
    model: str | None = None
    max_rows: int | None = Field(default=None, ge=1, le=10000)
    concurrency: int = Field(default=8, ge=1, le=32)


class PrepTaskResponse(BaseModel):
    """Acknowledgement for an async data-prep operation."""

    task_id: str
    operation: str
    message: str = ""
