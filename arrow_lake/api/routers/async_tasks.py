"""Unified async task endpoints — fire-and-forget for heavy operations.

Provides a single set of async variants for ingest, backup, quality,
and index creation operations.  All return task_id immediately (HTTP 202)
and can be polled via ``GET /api/v1/tasks/{task_id}/status``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_config, get_lake, require_role
from arrow_lake.api.tasks import TaskManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["async-tasks"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class AsyncTaskResponse(BaseModel):
    """Generic response for fire-and-forget async operations."""
    task_id: str
    operation: str
    status: str = "pending"
    message: str = ""


class AsyncTaskStatusResponse(BaseModel):
    """Generic status response for any background task."""
    task_id: str
    operation: str
    status: str
    progress: float = 0.0
    created_at: str = ""
    completed_at: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    detail: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Generic task status endpoints
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/status", response_model=AsyncTaskStatusResponse)
async def get_task_status(
    task_id: str = Path(...),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> AsyncTaskStatusResponse:
    """Get the status of any background task."""
    task = TaskManager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return AsyncTaskStatusResponse(
        task_id=task.task_id,
        operation=task.operation,
        status=task.status.value,
        progress=task.progress,
        created_at=task.created_at,
        completed_at=task.completed_at,
        error=task.error,
        result=task.result,
        detail=task.detail or None,
    )


@router.get("/tasks")
async def list_tasks(
    _user: dict = Depends(require_role(Role.VIEWER)),
    operation: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """List background tasks, optionally filtered."""
    tasks = TaskManager.list_tasks(operation=operation, status=status)
    return {
        "total": len(tasks),
        "tasks": [
            {
                "task_id": t.task_id,
                "operation": t.operation,
                "dataset_name": t.dataset_name,
                "status": t.status.value,
                "progress": t.progress,
                "created_at": t.created_at,
                "completed_at": t.completed_at,
                "error": t.error,
            }
            for t in tasks
        ],
    }


# ---------------------------------------------------------------------------
# Async ingest
# ---------------------------------------------------------------------------


class AsyncIngestRequest(BaseModel):
    """Request body for async file ingest."""
    file_paths: list[str] = Field(default_factory=list)
    blob_keys: list[str] = Field(default_factory=list)
    transforms: list[dict[str, Any]] | None = None


@router.post(
    "/datasets/{name}/ingest/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def ingest_files_async(
    name: str = Path(..., pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$"),
    *,
    req: AsyncIngestRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async file ingest — returns task_id immediately."""
    all_paths = list(req.file_paths) + list(req.blob_keys)
    transforms = None
    if req.transforms:
        from arrow_lake.ingest.transforms import build_transforms
        transforms = build_transforms(req.transforms)

    task_id = TaskManager.create_task("ingest", name)
    _task = asyncio.create_task(  # noqa: RUF006
        TaskManager.run_background(task_id, lake.ingest, name, all_paths, transforms=transforms)
    )
    return AsyncTaskResponse(
        task_id=task_id, operation="ingest",
        message=f"Async ingest started for dataset '{name}'",
    )


# ---------------------------------------------------------------------------
# Async backup
# ---------------------------------------------------------------------------


class AsyncBackupRequest(BaseModel):
    datasets: list[str] = Field(default_factory=list)


@router.post(
    "/backup/create/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def backup_create_async(
    *,
    req: AsyncBackupRequest,
    config=Depends(get_config),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> AsyncTaskResponse:
    """Async backup — returns task_id immediately."""
    from arrow_lake.ops.backup import BackupManager, BlobStoreManager

    blob_store = BlobStoreManager(config.storage)
    mgr = BackupManager(
        storage_config=config.storage,
        lance_base_uri=config.storage.base_uri,
        blob_store=blob_store,
    )

    task_id = TaskManager.create_task("backup", detail={"datasets": req.datasets})
    _task = asyncio.create_task(  # noqa: RUF006
        TaskManager.run_background(task_id, mgr.create_backup, req.datasets)
    )
    return AsyncTaskResponse(
        task_id=task_id, operation="backup",
        message=f"Async backup started for {len(req.datasets)} datasets",
    )


class AsyncRestoreRequest(BaseModel):
    backup_id: str
    datasets: list[str] | None = None


@router.post(
    "/backup/restore/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def backup_restore_async(
    *,
    req: AsyncRestoreRequest,
    config=Depends(get_config),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> AsyncTaskResponse:
    """Async restore — returns task_id immediately."""
    from arrow_lake.ops.backup import BackupManager, BlobStoreManager

    blob_store = BlobStoreManager(config.storage)
    mgr = BackupManager(
        storage_config=config.storage,
        lance_base_uri=config.storage.base_uri,
        blob_store=blob_store,
    )

    task_id = TaskManager.create_task("restore", detail={"backup_id": req.backup_id})
    _task = asyncio.create_task(  # noqa: RUF006
        TaskManager.run_background(
            task_id, mgr.restore_backup, req.backup_id, req.datasets or []
        )
    )
    return AsyncTaskResponse(
        task_id=task_id, operation="restore",
        message=f"Async restore started from backup '{req.backup_id}'",
    )
