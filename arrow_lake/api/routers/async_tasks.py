"""Unified async task endpoints — fire-and-forget for heavy operations.

Provides a single set of async variants for ingest, backup, quality,
and index creation operations.  All return task_id immediately (HTTP 202)
and can be polled via ``GET /api/v1/tasks/{task_id}/status``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
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
    # IDOR:非 ADMIN 仅能查自己创建的任务(user_id 未设的旧任务兼容放行)
    if getattr(_user, "role", None) != Role.ADMIN:
        _uid = getattr(_user, "user_id", None)
        if task.user_id is not None and task.user_id != _uid:
            raise HTTPException(status_code=403, detail="无权访问该任务")
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
    # IDOR:非 ADMIN 仅列自己创建的任务(user_id 未设的旧任务兼容放行)
    if getattr(_user, "role", None) != Role.ADMIN:
        _uid = getattr(_user, "user_id", None)
        tasks = [t for t in tasks if t.user_id is None or t.user_id == _uid]
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

    from arrow_lake.api._security_log import actor_of
    actor = actor_of(_user)
    task_id = TaskManager.create_task("ingest", name, user_id=_user.user_id)
    _task = asyncio.create_task(  # noqa: RUF006
        TaskManager.run_background(task_id, lake.ingest, name, all_paths, transforms=transforms, actor=actor)
    )
    return AsyncTaskResponse(
        task_id=task_id, operation="ingest",
        message=f"Async ingest started for dataset '{name}'",
    )


class AsyncDocumentsIngestRequest(BaseModel):
    """Request body for async documents ingest (parse → chunk → embed → FTS)."""

    pdf_paths: list[str] = Field(default_factory=list)
    blob_keys: list[str] = Field(default_factory=list)
    doc_type: str | None = None


def _bg_ingest_documents(
    app_state: Any,
    name: str,
    pdf_paths: list[str],
    blob_keys: list[str],
    doc_type: str | None,
    lake: Any,
    actor: str = "system",
) -> Any:
    """Background worker for the full documents ingest flow.

    Mirrors the synchronous ``/ingest/documents`` (resolve blobs → parse/chunk
    → embed → FTS → after-hooks) but runs in the executor via
    ``TaskManager.run_background`` so the request returns immediately. The
    tmp_dir lifetime is scoped to THIS task (not the request handler) so the
    downloaded blob files survive after the 202 response is sent.
    """
    import shutil
    import tempfile

    from arrow_lake.api.routers.datasets import _after_ingest_hooks, _resolve_blob_keys

    log = logging.getLogger(__name__)
    tmp_dir: str | None = None
    try:
        all_paths = list(pdf_paths)
        if blob_keys:
            tmp_dir = tempfile.mkdtemp(prefix="al_ingest_")
            all_paths.extend(_resolve_blob_keys(blob_keys, lake, tmp_dir))
        doc_config = lake._config.document if hasattr(lake, "_config") else None
        report = lake.ingest_documents(
            name, all_paths, doc_config=doc_config, doc_type=doc_type, actor=actor
        )
        # Best-effort post-steps (mirror sync endpoint): never fail the task on
        # embedding / FTS index errors — text_content + FTS still work without them.
        # v1.9.5: create_vector_index added so hybrid RAG works out-of-the-box.
        # IVF_PQ requires ≥256 rows; smaller datasets raise VECTOR_INDEX_TOO_FEW_ROWS
        # (caught here → WARN skip; vector strategy still works via brute-force).
        for step_fn, label in (
            (getattr(lake, "embed_and_add", None), "embed_documents"),
            (getattr(lake, "create_fts_index", None), "create_fts_index"),
            (getattr(lake, "create_vector_index", None), "create_vector_index"),
        ):
            if callable(step_fn):
                try:
                    step_fn(name)
                except Exception as exc:  # noqa: BLE001
                    log.warning("ingest.post_step_failed", dataset=name, step=label, err=str(exc)[:160])
        _after_ingest_hooks(app_state, name, lake)
        return report
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post(
    "/datasets/{name}/ingest/documents/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def ingest_documents_async(
    name: str = Path(..., pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$"),
    *,
    req: AsyncDocumentsIngestRequest,
    request: Request,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async documents ingest — returns task_id immediately (HTTP 202).

    Same flow as the synchronous ``/ingest/documents`` (parse → chunk → embed →
    FTS) but runs in the background so the client doesn't hold the connection
    open for the full ingest. Poll via ``GET /api/v1/tasks/{task_id}/status``
    or watch on the tasks queue page (``tasks.html?task=<task_id>``).
    """
    from arrow_lake.api._security_log import actor_of
    actor = actor_of(_user)
    task_id = TaskManager.create_task("ingest_documents", name, user_id=_user.user_id)
    asyncio.create_task(  # noqa: RUF006
        TaskManager.run_background(
            task_id, _bg_ingest_documents,
            request.app.state, name, req.pdf_paths, req.blob_keys, req.doc_type, lake, actor,
        )
    )
    return AsyncTaskResponse(
        task_id=task_id, operation="ingest_documents",
        message=f"Async documents ingest started for dataset '{name}'",
    )


# ---------------------------------------------------------------------------
# Async index creation
# ---------------------------------------------------------------------------


class AsyncVectorIndexRequest(BaseModel):
    """Request body for async vector index creation."""

    metric: str = ""
    vector_column: str = "text_embedding"
    index_type: str = ""
    num_partitions: int | None = None
    num_sub_vectors: int | None = None
    replace: bool = True


class AsyncFtsIndexRequest(BaseModel):
    """Request body for async FTS index creation."""

    fts_column: str | None = None
    replace: bool = True


def _bg_create_vector_index(lake: Any, name: str, **kwargs: Any) -> Any:
    return lake.create_vector_index(name, **kwargs)


def _bg_create_fts_index(lake: Any, name: str, **kwargs: Any) -> None:
    lake.create_fts_index(name, **kwargs)


@router.post(
    "/datasets/{name}/index/vector/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def create_vector_index_async(
    name: str = Path(..., pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$"),
    *,
    req: AsyncVectorIndexRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async vector index creation — returns task_id immediately (HTTP 202).

    Vector index builds (IVF_PQ/HNSW) can run minutes on large datasets; this
    avoids blocking the client. Poll via /tasks/{task_id}/status.
    """
    task_id = TaskManager.create_task(
        "create_vector_index", name, user_id=_user.user_id,
        detail={
            "metric": req.metric, "vector_column": req.vector_column,
            "index_type": req.index_type, "num_partitions": req.num_partitions,
            "num_sub_vectors": req.num_sub_vectors,
        },
    )
    asyncio.create_task(  # noqa: RUF006
        TaskManager.run_background(
            task_id, _bg_create_vector_index, lake, name,
            metric=req.metric, vector_column=req.vector_column, index_type=req.index_type,
            num_partitions=req.num_partitions, num_sub_vectors=req.num_sub_vectors, replace=req.replace,
        )
    )
    return AsyncTaskResponse(
        task_id=task_id, operation="create_vector_index",
        message=f"Async vector index build started for dataset '{name}'",
    )


@router.post(
    "/datasets/{name}/index/fts/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def create_fts_index_async(
    name: str = Path(..., pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$"),
    *,
    req: AsyncFtsIndexRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async FTS index creation — returns task_id immediately (HTTP 202)."""
    task_id = TaskManager.create_task(
        "create_fts_index", name, user_id=_user.user_id,
        detail={"fts_column": req.fts_column},
    )
    asyncio.create_task(  # noqa: RUF006
        TaskManager.run_background(
            task_id, _bg_create_fts_index, lake, name,
            fts_column=req.fts_column, replace=req.replace,
        )
    )
    return AsyncTaskResponse(
        task_id=task_id, operation="create_fts_index",
        message=f"Async FTS index build started for dataset '{name}'",
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

    task_id = TaskManager.create_task("backup", detail={"datasets": req.datasets}, user_id=_user.user_id)
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

    task_id = TaskManager.create_task("restore", detail={"backup_id": req.backup_id}, user_id=_user.user_id)
    _task = asyncio.create_task(  # noqa: RUF006
        TaskManager.run_background(
            task_id, mgr.restore_backup, req.backup_id, req.datasets or []
        )
    )
    return AsyncTaskResponse(
        task_id=task_id, operation="restore",
        message=f"Async restore started from backup '{req.backup_id}'",
    )
