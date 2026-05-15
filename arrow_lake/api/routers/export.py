"""Export endpoints: async dataset export with task tracking."""

from __future__ import annotations

import asyncio
from pathlib import Path as FilePath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.models.common import _NAME_PATTERN
from arrow_lake.api.models.query import ExportRequest, ExportTaskResponse, ExportTaskStatusResponse
from arrow_lake.api.tasks import TaskManager
from arrow_lake.api.utils import run_sync

router = APIRouter(prefix="/api/v1/datasets", tags=["export"])


@router.post("/{name}/export", response_model=ExportTaskResponse, status_code=202)
async def export_dataset(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: ExportRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> ExportTaskResponse:
    """Export a dataset to Parquet or CSV (async, returns task_id)."""
    task_id = TaskManager.create_task(name, req.output_path, fmt=req.format or "parquet")

    export_kwargs = {
        "format": req.format,
        "columns": req.columns,
        "version": req.version,
        "compression": req.compression,
        "overwrite": req.overwrite,
    }

    _task = asyncio.create_task(  # noqa: RUF006 — TaskManager tracks lifetime
        TaskManager.run_export(task_id, lake, **export_kwargs)
    )

    return ExportTaskResponse(
        task_id=task_id,
        dataset_name=name,
        status="pending",
        message="Export task queued",
    )


@router.get("/{name}/export/{task_id}/status", response_model=ExportTaskStatusResponse)
async def get_export_status(
    name: str = Path(..., pattern=_NAME_PATTERN),
    task_id: str = Path(...),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> ExportTaskStatusResponse:
    """Check the status of an async export task."""
    task = TaskManager.get_task(task_id)
    if task is None or task.dataset_name != name:
        raise HTTPException(status_code=404, detail="Export task not found")

    return ExportTaskStatusResponse(
        task_id=task.task_id,
        status=task.status.value,
        progress=task.progress,
        created_at=task.created_at,
        completed_at=task.completed_at,
        error=task.error,
        result=task.result,
    )


@router.get("/{name}/export/{task_id}/download")
async def download_export(
    name: str = Path(..., pattern=_NAME_PATTERN),
    task_id: str = Path(...),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> None:
    """Download an exported file (only available after task completes)."""
    from starlette.responses import FileResponse

    from arrow_lake.api.deps import get_config

    task = TaskManager.get_task(task_id)
    if task is None or task.dataset_name != name:
        raise HTTPException(status_code=404, detail="Export task not found")
    if task.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Export not completed (status: {task.status})",
        )

    cfg = get_config()
    base_dir = getattr(cfg.export, "base_dir", "/app/exports")
    output = FilePath(task.output_path)
    if output.is_absolute():
        raise HTTPException(status_code=400, detail="Absolute paths not allowed")
    base_resolved = FilePath(base_dir).resolve()
    file_path = (base_resolved / output).resolve()
    if not file_path.is_relative_to(base_resolved):
        raise HTTPException(status_code=403, detail="Path escapes base directory")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Export file not found on disk")

    content_type = "text/csv" if task.fmt == "csv" else "application/octet-stream"
    filename = output.name
    return FileResponse(
        str(file_path),
        media_type=content_type,
        filename=filename,
    )


# ---------------------------------------------------------------------------
# Daft multi-target export (sync)
# ---------------------------------------------------------------------------

_SUPPORTED_EXPORT_FORMATS = ("parquet", "csv", "json", "iceberg", "clickhouse")


class ExportToRequest(BaseModel):
    """Request body for Daft multi-target export."""

    target_uri: str = Field(..., min_length=1, description="Target URI")
    format: str = Field(..., description=f"Export format: {', '.join(_SUPPORTED_EXPORT_FORMATS)}")
    options: dict[str, Any] | None = Field(default=None, description="Format-specific options")


@router.post("/{name}/export-to")
async def export_to(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: ExportToRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> dict[str, Any]:
    """Export dataset to an external target via Daft (sync)."""
    if req.format not in _SUPPORTED_EXPORT_FORMATS:
        raise HTTPException(400, f"Unsupported format: {req.format}. Use {_SUPPORTED_EXPORT_FORMATS}")
    kwargs = req.options or {}
    result = await run_sync(
        lake.export_to, name,
        target_uri=req.target_uri, format=req.format,
        timeout=300, label="export_to",
        **kwargs,
    )
    return {"success": True, **result}
