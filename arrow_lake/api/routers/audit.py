"""Audit trail management endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.models.audit import (
    AuditExportResponse,
    AuditQueryResponse,
    AuditRecordRequest,
    AuditRecordResponse,
    AuditVerifyResponse,
)
from arrow_lake.api.utils import run_sync

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

_AUDIT_TIMEOUT = 60


@router.post("/record", response_model=AuditRecordResponse)
async def audit_record(
    *,
    req: AuditRecordRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AuditRecordResponse:
    """Record an audit event."""
    audit_id = await run_sync(
        lake.audit_record,
        event_type=req.event_type,
        dataset_name=req.dataset_name,
        actor=req.actor,
        lance_version=req.lance_version,
        metaflow_run_id=req.metaflow_run_id,
        metaflow_tags=req.metaflow_tags,
        payload=req.payload,
        timeout=_AUDIT_TIMEOUT,
        label="audit_record",
    )
    return AuditRecordResponse(audit_id=audit_id)


@router.post("/verify", response_model=AuditVerifyResponse)
async def audit_verify(
    audit_id: str,
    *,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> AuditVerifyResponse:
    """Verify the integrity of an audit entry."""
    intact = await run_sync(
        lake.audit_verify, audit_id,
        timeout=_AUDIT_TIMEOUT, label="audit_verify",
    )
    return AuditVerifyResponse(intact=intact)


@router.get("/query", response_model=AuditQueryResponse)
async def audit_query(
    dataset_name: str | None = None,
    start: str | None = None,
    end: str | None = None,
    event_type: str | None = None,
    *,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> AuditQueryResponse:
    """Query audit trail entries with optional filters."""
    entries = await run_sync(
        lake.audit_query,
        dataset_name=dataset_name,
        start=start,
        end=end,
        event_type=event_type,
        timeout=_AUDIT_TIMEOUT,
        label="audit_query",
    )
    serialized: list[dict[str, Any]] = [
        e if isinstance(e, dict) else {"entry": str(e)} for e in entries
    ]
    return AuditQueryResponse(entries=serialized)


@router.post("/export", response_model=AuditExportResponse)
async def audit_export(
    dataset_name: str,
    *,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> AuditExportResponse:
    """Export audit trail for a dataset."""
    result = await run_sync(
        lake.audit_export, dataset_name,
        timeout=_AUDIT_TIMEOUT, label="audit_export",
    )
    return AuditExportResponse(export=result)
