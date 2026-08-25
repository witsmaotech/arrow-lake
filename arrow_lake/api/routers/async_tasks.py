"""Unified async task endpoints — fire-and-forget for heavy operations.

Provides a single set of async variants for ingest, backup, quality,
and index creation operations.  All return task_id immediately (HTTP 202)
and can be polled via ``GET /api/v1/tasks/{task_id}/status``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from arrow_lake.api._security_log import actor_of
from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import authorize_dataset, get_config, get_lake, require_role
from arrow_lake.api.models.dataset import (
    IngestDeltaLakeRequest,
    IngestDocumentsRequest,
    IngestFilesRequest,
    IngestHttpRequest,
    IngestIcebergRequest,
    IngestImagesRequest,
    IngestKafkaRequest,
    IngestMixedRequest,
    IngestSqlRequest,
    IngestVideosRequest,
)
from arrow_lake.api.tasks import TaskManager, spawn_background

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

# DR14 W1.3: container table names follow the storage identifier rules
# (strict subset of _INGEST_NAME_PATTERN; storage re-validates anyway).
_TABLE_NAME_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_-]*$"


class AsyncIngestRequest(IngestFilesRequest):
    """Async file ingest (= sync IngestFilesRequest + optional description)."""

    description: str | None = Field(default=None, max_length=1000)
    # DR14 W1.3: optional container table target (structured file sources).
    table: str | None = Field(default=None, pattern=_TABLE_NAME_PATTERN)


class AsyncDocumentsIngestRequest(IngestDocumentsRequest):
    """Async documents ingest (= sync IngestDocumentsRequest + description)."""

    description: str | None = Field(default=None, max_length=1000)


class AsyncIngestSqlRequest(IngestSqlRequest):
    description: str | None = Field(default=None, max_length=1000)
    # DR14 W1.3: optional container table target.
    table: str | None = Field(default=None, pattern=_TABLE_NAME_PATTERN)


class AsyncIngestKafkaRequest(IngestKafkaRequest):
    description: str | None = Field(default=None, max_length=1000)


class AsyncIngestIcebergRequest(IngestIcebergRequest):
    description: str | None = Field(default=None, max_length=1000)


class AsyncIngestDeltaLakeRequest(IngestDeltaLakeRequest):
    description: str | None = Field(default=None, max_length=1000)


class AsyncIngestHttpRequest(IngestHttpRequest):
    description: str | None = Field(default=None, max_length=1000)


class AsyncIngestImagesRequest(IngestImagesRequest):
    description: str | None = Field(default=None, max_length=1000)


class AsyncIngestVideosRequest(IngestVideosRequest):
    description: str | None = Field(default=None, max_length=1000)


class AsyncIngestMixedRequest(IngestMixedRequest):
    description: str | None = Field(default=None, max_length=1000)


def _build_transforms(spec: list[dict[str, Any]] | None) -> Any:
    if not spec:
        return None
    from arrow_lake.ingest.transforms import build_transforms

    return build_transforms(spec)


def _finalize_ingest(
    app_state: Any, name: str, lake: Any, description: str | None,
) -> None:
    """Shared post-ingest tail: Gravitino/cache hooks + description persist.

    ``description`` is written here (after the dataset exists) rather than via a
    separate client ``PUT /description``, which raced dataset creation on the
    async path and silently 422'd.
    """
    from arrow_lake.api.routers.datasets import _after_ingest_hooks, _save_desc

    _after_ingest_hooks(app_state, name, lake)
    if description:
        _save_desc(name, description)


def _run_ingest_async(
    name: str, operation: str, bg_fn: Any, bg_args: tuple, user_id: int | None,
) -> AsyncTaskResponse:
    """Create a task, spawn the background worker (strong ref), return 202."""
    task_id = TaskManager.create_task(operation, name, user_id=user_id)
    spawn_background(TaskManager.run_background(task_id, bg_fn, *bg_args))
    return AsyncTaskResponse(
        task_id=task_id, operation=operation,
        message=f"Async {operation} started for dataset '{name}'",
    )


def _bg_ingest_files(
    app_state: Any, name: str, file_paths: list[str], blob_keys: list[str],
    transforms_spec: list[dict[str, Any]] | None, lake: Any, actor: str,
    description: str | None, table: str | None = None,
) -> Any:
    import shutil
    import tempfile

    from arrow_lake.api.routers.datasets import _resolve_blob_keys_smart

    all_paths = list(file_paths)
    tmp_dir: str | None = None
    try:
        if blob_keys:
            tmp_dir = tempfile.mkdtemp(prefix="al_ingest_")
            s3_uris, local_paths = _resolve_blob_keys_smart(blob_keys, lake, tmp_dir)
            all_paths.extend(s3_uris)
            all_paths.extend(local_paths)
        report = lake.ingest(
            name, all_paths, transforms=_build_transforms(transforms_spec), actor=actor,
            table=table,
        )
        _finalize_ingest(app_state, name, lake, description)
        return report
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _bg_ingest_documents(
    app_state: Any, name: str, pdf_paths: list[str], blob_keys: list[str],
    doc_type: str | None, lake: Any, actor: str, description: str | None,
) -> Any:
    """Background documents ingest (resolve blobs → parse/chunk → embed → FTS
    → after-hooks). tmp_dir lifetime is scoped to THIS task so downloaded blobs
    survive the 202 response.
    """
    import shutil
    import tempfile

    from arrow_lake.api.routers.datasets import _resolve_blob_keys

    tmp_dir: str | None = None
    try:
        all_paths = list(pdf_paths)
        if blob_keys:
            tmp_dir = tempfile.mkdtemp(prefix="al_ingest_")
            all_paths.extend(_resolve_blob_keys(blob_keys, lake, tmp_dir))
        doc_config = lake._config.document if hasattr(lake, "_config") else None
        report = lake.ingest_documents_and_index(
            name, all_paths, doc_config=doc_config, doc_type=doc_type, actor=actor
        )
        _finalize_ingest(app_state, name, lake, description)
        return report
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _bg_ingest_sql(
    app_state: Any, name: str, sql: str, connection_url: str,
    partition_col: str | None, num_partitions: int | None,
    transforms_spec: list[dict[str, Any]] | None, lake: Any, actor: str,
    description: str | None, table: str | None = None,
) -> Any:
    report = lake.ingest_sql(
        name, sql=sql, connection_url=connection_url, partition_col=partition_col,
        num_partitions=num_partitions, transforms=_build_transforms(transforms_spec), actor=actor,
        table=table,
    )
    _finalize_ingest(app_state, name, lake, description)
    return report


def _bg_ingest_kafka(
    app_state: Any, name: str, bootstrap_servers: str, topics: list[str],
    start: str, end: str, json_decode: bool,
    transforms_spec: list[dict[str, Any]] | None, lake: Any, actor: str,
    description: str | None,
) -> Any:
    report = lake.ingest_kafka(
        name, bootstrap_servers=bootstrap_servers, topics=topics, start=start,
        end=end, json_decode=json_decode, transforms=_build_transforms(transforms_spec), actor=actor,
    )
    _finalize_ingest(app_state, name, lake, description)
    return report


def _bg_ingest_iceberg(
    app_state: Any, name: str, table_uri: str,
    transforms_spec: list[dict[str, Any]] | None, lake: Any, actor: str,
    description: str | None,
) -> Any:
    report = lake.ingest_iceberg(
        name, table_uri=table_uri, transforms=_build_transforms(transforms_spec), actor=actor,
    )
    _finalize_ingest(app_state, name, lake, description)
    return report


def _bg_ingest_deltalake(
    app_state: Any, name: str, table_uri: str, version: int | None,
    transforms_spec: list[dict[str, Any]] | None, lake: Any, actor: str,
    description: str | None,
) -> Any:
    report = lake.ingest_deltalake(
        name, table_uri=table_uri, version=version,
        transforms=_build_transforms(transforms_spec), actor=actor,
    )
    _finalize_ingest(app_state, name, lake, description)
    return report


def _bg_ingest_http(
    app_state: Any, name: str, urls: list[str], lake: Any, actor: str,
    description: str | None,
) -> Any:
    report = lake.ingest_http(name, urls, actor=actor)
    _finalize_ingest(app_state, name, lake, description)
    return report


def _bg_ingest_media(
    app_state: Any, name: str, file_paths: list[str], blob_keys: list[str],
    lake: Any, actor: str, description: str | None, kind: str,
) -> Any:
    """Shared background worker for images/videos (local-path blob resolution +
    best-effort CLIP embed). ``kind`` ∈ {"images", "videos"}."""
    import shutil
    import tempfile

    from arrow_lake.api.routers.datasets import _resolve_blob_keys

    all_paths = list(file_paths)
    tmp_dir: str | None = None
    try:
        if blob_keys:
            tmp_dir = tempfile.mkdtemp(prefix="al_ingest_")
            # Pillow/av require local paths
            all_paths.extend(_resolve_blob_keys(blob_keys, lake, tmp_dir))
        if kind == "images":
            report = lake.ingest_images(name, all_paths, actor=actor)
            embed_col = "image_data"
        else:
            report = lake.ingest_videos(name, all_paths, actor=actor)
            embed_col = "video_data"
        try:
            lake.embed_media(name, image_column=embed_col)
        except Exception:  # noqa: BLE001 — missing CLIP model must not fail ingest
            pass
        _finalize_ingest(app_state, name, lake, description)
        return report
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _bg_ingest_mixed(
    app_state: Any, name: str,
    sources: dict[str, list[str]], blob_keys: dict[str, list[str]],
    lake: Any, actor: str, description: str | None,
) -> Any:
    import shutil
    import tempfile

    from arrow_lake.api.routers.datasets import _resolve_blob_sources

    sources = dict(sources)
    tmp_dir: str | None = None
    try:
        if blob_keys:
            tmp_dir = tempfile.mkdtemp(prefix="al_ingest_")
            resolved = _resolve_blob_sources(blob_keys, lake, tmp_dir)
            for modality, paths in resolved.items():
                sources.setdefault(modality, []).extend(paths)
        report = lake.ingest_mixed(name, sources, actor=actor)
        _finalize_ingest(app_state, name, lake, description)
        return report
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


_INGEST_NAME_PATTERN = r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$"


@router.post(
    "/datasets/{name}/ingest/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def ingest_files_async(
    name: str = Path(..., pattern=_INGEST_NAME_PATTERN),
    *,
    req: AsyncIngestRequest,
    request: Request,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async file ingest — returns task_id immediately (HTTP 202)."""
    authorize_dataset(request, name, write=True)
    return _run_ingest_async(
        name, "ingest", _bg_ingest_files,
        (request.app.state, name, req.file_paths, req.blob_keys, req.transforms, lake, actor_of(_user), req.description, req.table),
        _user.user_id,
    )


@router.post(
    "/datasets/{name}/ingest/documents/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def ingest_documents_async(
    name: str = Path(..., pattern=_INGEST_NAME_PATTERN),
    *,
    req: AsyncDocumentsIngestRequest,
    request: Request,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async documents ingest (parse → chunk → embed → FTS) — 202 immediately.

    Poll via ``GET /api/v1/tasks/{task_id}/status`` or the tasks queue page.
    """
    authorize_dataset(request, name, write=True)
    return _run_ingest_async(
        name, "ingest_documents", _bg_ingest_documents,
        (request.app.state, name, req.pdf_paths, req.blob_keys, req.doc_type, lake, actor_of(_user), req.description),
        _user.user_id,
    )


@router.post(
    "/datasets/{name}/ingest/sql/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def ingest_sql_async(
    name: str = Path(..., pattern=_INGEST_NAME_PATTERN),
    *,
    req: AsyncIngestSqlRequest,
    request: Request,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async SQL ingest — returns task_id immediately."""
    authorize_dataset(request, name, write=True)
    return _run_ingest_async(
        name, "ingest_sql", _bg_ingest_sql,
        (request.app.state, name, req.sql, req.connection_url, req.partition_col,
         req.num_partitions, req.transforms, lake, actor_of(_user), req.description, req.table),
        _user.user_id,
    )


@router.post(
    "/datasets/{name}/ingest/kafka/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def ingest_kafka_async(
    name: str = Path(..., pattern=_INGEST_NAME_PATTERN),
    *,
    req: AsyncIngestKafkaRequest,
    request: Request,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async Kafka ingest — returns task_id immediately."""
    authorize_dataset(request, name, write=True)
    return _run_ingest_async(
        name, "ingest_kafka", _bg_ingest_kafka,
        (request.app.state, name, req.bootstrap_servers, req.topics, req.start,
         req.end, req.json_decode, req.transforms, lake, actor_of(_user), req.description),
        _user.user_id,
    )


@router.post(
    "/datasets/{name}/ingest/iceberg/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def ingest_iceberg_async(
    name: str = Path(..., pattern=_INGEST_NAME_PATTERN),
    *,
    req: AsyncIngestIcebergRequest,
    request: Request,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async Iceberg ingest — returns task_id immediately."""
    authorize_dataset(request, name, write=True)
    return _run_ingest_async(
        name, "ingest_iceberg", _bg_ingest_iceberg,
        (request.app.state, name, req.table_uri, req.transforms, lake, actor_of(_user), req.description),
        _user.user_id,
    )


@router.post(
    "/datasets/{name}/ingest/deltalake/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def ingest_deltalake_async(
    name: str = Path(..., pattern=_INGEST_NAME_PATTERN),
    *,
    req: AsyncIngestDeltaLakeRequest,
    request: Request,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async Delta Lake ingest — returns task_id immediately."""
    authorize_dataset(request, name, write=True)
    return _run_ingest_async(
        name, "ingest_deltalake", _bg_ingest_deltalake,
        (request.app.state, name, req.table_uri, req.version, req.transforms, lake, actor_of(_user), req.description),
        _user.user_id,
    )


@router.post(
    "/datasets/{name}/ingest/http/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def ingest_http_async(
    name: str = Path(..., pattern=_INGEST_NAME_PATTERN),
    *,
    req: AsyncIngestHttpRequest,
    request: Request,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async HTTP URL ingest — returns task_id immediately."""
    authorize_dataset(request, name, write=True)
    return _run_ingest_async(
        name, "ingest_http", _bg_ingest_http,
        (request.app.state, name, req.urls, lake, actor_of(_user), req.description),
        _user.user_id,
    )


@router.post(
    "/datasets/{name}/ingest/images/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def ingest_images_async(
    name: str = Path(..., pattern=_INGEST_NAME_PATTERN),
    *,
    req: AsyncIngestImagesRequest,
    request: Request,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async image ingest — returns task_id immediately."""
    authorize_dataset(request, name, write=True)
    return _run_ingest_async(
        name, "ingest_images", _bg_ingest_media,
        (request.app.state, name, req.file_paths, req.blob_keys, lake, actor_of(_user), req.description, "images"),
        _user.user_id,
    )


@router.post(
    "/datasets/{name}/ingest/videos/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def ingest_videos_async(
    name: str = Path(..., pattern=_INGEST_NAME_PATTERN),
    *,
    req: AsyncIngestVideosRequest,
    request: Request,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async video ingest — returns task_id immediately."""
    authorize_dataset(request, name, write=True)
    return _run_ingest_async(
        name, "ingest_videos", _bg_ingest_media,
        (request.app.state, name, req.file_paths, req.blob_keys, lake, actor_of(_user), req.description, "videos"),
        _user.user_id,
    )


@router.post(
    "/datasets/{name}/ingest/mixed/async",
    response_model=AsyncTaskResponse,
    status_code=202,
)
async def ingest_mixed_async(
    name: str = Path(..., pattern=_INGEST_NAME_PATTERN),
    *,
    req: AsyncIngestMixedRequest,
    request: Request,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async mixed-modality ingest — returns task_id immediately."""
    authorize_dataset(request, name, write=True)
    return _run_ingest_async(
        name, "ingest_mixed", _bg_ingest_mixed,
        (request.app.state, name, req.sources, req.blob_keys, lake, actor_of(_user), req.description),
        _user.user_id,
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
    request: Request,
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
    authorize_dataset(request, name, write=True)
    task_id = TaskManager.create_task(
        "create_vector_index", name, user_id=_user.user_id,
        detail={
            "metric": req.metric, "vector_column": req.vector_column,
            "index_type": req.index_type, "num_partitions": req.num_partitions,
            "num_sub_vectors": req.num_sub_vectors,
        },
    )
    spawn_background(
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
    request: Request,
    name: str = Path(..., pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$"),
    *,
    req: AsyncFtsIndexRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> AsyncTaskResponse:
    """Async FTS index creation — returns task_id immediately (HTTP 202)."""
    authorize_dataset(request, name, write=True)
    task_id = TaskManager.create_task(
        "create_fts_index", name, user_id=_user.user_id,
        detail={"fts_column": req.fts_column},
    )
    spawn_background(
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
    spawn_background(
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
    spawn_background(
        TaskManager.run_background(
            task_id, mgr.restore_backup, req.backup_id, req.datasets or []
        )
    )
    return AsyncTaskResponse(
        task_id=task_id, operation="restore",
        message=f"Async restore started from backup '{req.backup_id}'",
    )
