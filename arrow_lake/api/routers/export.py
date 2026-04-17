"""Export endpoints: async dataset export with task tracking."""

from __future__ import annotations

import asyncio
from pathlib import Path as FilePath

from fastapi import APIRouter, Depends, HTTPException, Path

from arrow_lake.api.deps import get_lake
from arrow_lake.api.models.common import _NAME_PATTERN
from arrow_lake.api.models.query import ExportRequest, ExportTaskResponse, ExportTaskStatusResponse
from arrow_lake.api.tasks import TaskManager

router = APIRouter(prefix="/api/v1/datasets", tags=["export"])


@router.post("/{name}/export", response_model=ExportTaskResponse, status_code=202)
async def export_dataset(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: ExportRequest,
    lake=Depends(get_lake),
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
) -> None:
    """Download an exported file (only available after task completes)."""
    from starlette.responses import FileResponse

    task = TaskManager.get_task(task_id)
    if task is None or task.dataset_name != name:
        raise HTTPException(status_code=404, detail="Export task not found")
    if task.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Export not completed (status: {task.status})",
        )
    if not FilePath(task.output_path).exists():
        raise HTTPException(status_code=404, detail="Export file not found on disk")

    content_type = "text/csv" if task.fmt == "csv" else "application/octet-stream"
    filename = FilePath(task.output_path).name
    return FileResponse(
        task.output_path,
        media_type=content_type,
        filename=filename,
    )
