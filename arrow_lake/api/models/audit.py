"""Audit trail request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AuditRecordRequest(BaseModel):
    event_type: str = Field(..., min_length=1)
    dataset_name: str = ""
    actor: str = "system"
    lance_version: int | None = None
    metaflow_run_id: str = ""
    metaflow_tags: dict[str, str] | None = None
    payload: dict[str, Any] | None = None


class AuditRecordResponse(BaseModel):
    success: bool = True
    audit_id: str


class AuditVerifyResponse(BaseModel):
    success: bool = True
    intact: bool


class AuditQueryResponse(BaseModel):
    success: bool = True
    entries: list[dict[str, Any]]


class AuditExportResponse(BaseModel):
    success: bool = True
    export: dict[str, Any]
