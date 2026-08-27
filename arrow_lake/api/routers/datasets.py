"""Dataset management and ingestion endpoints."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import Any

from fastapi import APIRouter, Depends, File, Path, Query, Request, UploadFile

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import (
    authorize_dataset,
    authorize_dataset_read,
    get_lake,
    require_permission,
    require_role,
)
from arrow_lake.api.rbac import Permission
from arrow_lake.api._security_log import actor_of
from arrow_lake.api.models.common import _NAME_PATTERN, MessageResponse
from arrow_lake.api.models.dataset import (
    CleanupResponse,
    DatasetDescriptionRequest,
    DatasetInfo,
    DatasetListResponse,
    IngestDeltaLakeRequest,
    IngestDocumentsRequest,
    IngestFilesRequest,
    IngestHttpRequest,
    IngestIcebergRequest,
    IngestImagesRequest,
    IngestKafkaRequest,
    IngestMixedRequest,
    IngestResponse,
    IngestSqlRequest,
    IngestVideosRequest,
    SchemaField,
    SchemaResponse,
    SchemaAnnotateRequest,
    PresignedUpload,
    PresignRequest,
    PresignResponse,
    SchemaMigrationIssue,
    SchemaMigrationRequest,
    SchemaMigrationResponse,
    UploadedBlob,
    UploadResponse,
)
from arrow_lake.api.utils import ingest_executor, run_sync
from arrow_lake.exceptions import CatalogError, ErrorCode, QueryError
from arrow_lake._system_tables import is_internal_table, is_system_table

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])

_INGEST_TIMEOUT = 600
_ADMIN_TIMEOUT = 60
_DOWNLOAD_WORKERS = 4
# DR14: container table names (same rule as AsyncIngestRequest.table).
_TABLE_NAME_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_-]*$"
# Total budget for a blob-download batch. One slow/hung download (boto3 default
# retries amplify a flaky MinIO) must not stall the whole ingest; boto3's
# per-call BotoConfig timeout bounds each download, this bounds the aggregate.
_DOWNLOAD_TIMEOUT = 600


def _schema_field_dicts(schema: Any) -> list[dict[str, Any]]:
    """Project a Lance/Arrow schema into SchemaField kwargs, including comments.

    Column comments are stored in Arrow field metadata under the ``comment``
    key (written by ingest capture or the annotate endpoint); ``description``
    is accepted as a fallback written by some producers.
    """
    out: list[dict[str, Any]] = []
    for f in schema:
        md = f.metadata or {}
        raw = md.get(b"comment") or md.get(b"description")
        comment = raw.decode("utf-8", "replace").strip() if raw else ""
        out.append(
            {
                "name": f.name,
                "type": str(f.type),
                "nullable": bool(f.nullable),
                "comment": comment,
            }
        )
    return out


def _after_ingest_hooks(app_state: Any, dataset_name: str, lake: Any) -> None:
    """Best-effort post-ingest actions: Gravitino Fileset registration + [#step2-B]
    query-cache invalidation so appended rows are visible to subsequent queries."""
    import structlog
    log = structlog.get_logger(__name__)
    # 1) Gravitino Fileset registration
    bridge = getattr(app_state, "gravitino_bridge", None)
    if bridge is not None and bridge.enabled:
        try:
            location = f"s3a://arrow-lake/{dataset_name}.lance"
            bridge.register_dataset(dataset_name, location=location)
            log.info("gravitino_registered", dataset=dataset_name)
        except Exception as exc:
            log.warning("gravitino_register_failed", dataset=dataset_name, error=str(exc))
    # 2) [#step2-B] Invalidate cached OLAP/facet results (append changes results)
    invalidate = getattr(lake, "invalidate_query_cache", None)
    if callable(invalidate):
        try:
            invalidate(dataset_name)
        except Exception as exc:
            log.warning("query_cache_invalidate_failed", dataset=dataset_name, error=str(exc))


_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
_ALLOWED_CONTENT_PREFIXES = (
    "text/", "application/", "image/", "video/", "audio/",
    "multipart/",
)

# Filenames are only a readable suffix on the blob key — `_unique_blob_key`
# already prepends a uuid8 prefix for collision resistance, so here we just need
# a path-safe identifier. Collapses any run of chars outside [A-Za-z0-9._-]
# (spaces, parentheses, commas, …) into a single '_' instead of rejecting the
# upload outright (a strict allow-list 500'd on perfectly normal filenames like
# "Attention Is All You Need.pdf").
_UNSAFE_FILENAME_CHAR_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Extensions that Daft can read directly from S3 URIs.
_S3_NATIVE_EXTENSIONS = frozenset({".csv", ".json", ".jsonl", ".parquet"})


def _sanitize_filename(name: str) -> str:
    from arrow_lake.api.models.dataset import _check_no_traversal

    stripped = os.path.basename(name)
    _check_no_traversal(stripped)
    if not stripped:
        raise ValueError(f"Empty filename: {name!r}")
    safe = _UNSAFE_FILENAME_CHAR_RE.sub("_", stripped).strip("_")
    if not safe:
        raise ValueError(f"Invalid filename: {name!r}")
    return safe


def _unique_blob_key(dataset_name: str, filename: str) -> str:
    """Build a collision-resistant blob key: uploads/{ds}/{uuid8}_{filename}."""
    safe = _sanitize_filename(filename)
    prefix = uuid.uuid4().hex[:8]
    return f"uploads/{dataset_name}/{prefix}_{safe}"


def _get_blob_store(lake: Any) -> Any:
    from arrow_lake.storage.blob_store import BlobStoreManager

    sc = lake._config.storage
    return lake._get_component(
        "blob_store",
        # v1.9.5 批6: raw uploads go to the dedicated uploads bucket.
        lambda: BlobStoreManager(config=sc, bucket=sc.uploads_bucket),
    )


def _blob_key_to_s3_uri(key: str, lake: Any) -> str:
    sc = lake._config.storage
    return f"s3://{sc.s3_bucket}/{key}"


def _is_s3_native(key: str) -> bool:
    ext = os.path.splitext(key)[1].lower()
    return ext in _S3_NATIVE_EXTENSIONS


def _resolve_blob_keys(blob_keys: list[str], lake: Any, tmp_dir: str) -> list[str]:
    blob_store = _get_blob_store(lake)

    def _download_one(idx: int, key: str) -> str:
        filename = key.rsplit("/", 1)[-1]
        # Use index prefix to prevent filename collisions
        dest = os.path.join(tmp_dir, f"{idx:04d}_{filename}")
        blob_store.download_file(key, dest)
        if not os.path.exists(dest) or os.path.getsize(dest) == 0:
            raise OSError(f"Download verification failed for blob key: {key}")
        return dest

    try:
        paths: dict[int, str] = {}
        # Bound the batch: as_completed + a total budget so one slow/hung blob
        # download can't stall the whole ingest. Results are re-ordered to the
        # input order (downstream zips them with the source file list).
        with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as pool:
            futures = {pool.submit(_download_one, i, k): i for i, k in enumerate(blob_keys)}
            try:
                for fut in as_completed(futures, timeout=_DOWNLOAD_TIMEOUT):
                    paths[futures[fut]] = fut.result()
            except FuturesTimeoutError:
                for f in futures:
                    f.cancel()
                raise OSError(
                    f"Blob download exceeded {_DOWNLOAD_TIMEOUT}s "
                    f"(completed {len(paths)}/{len(blob_keys)})"
                )
        return [paths[i] for i in range(len(blob_keys))]
    except Exception:
        # Clean up partial downloads on failure
        for f in os.listdir(tmp_dir):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(tmp_dir, f))
        raise


def _resolve_blob_keys_smart(
    blob_keys: list[str],
    lake: Any,
    tmp_dir: str,
) -> tuple[list[str], list[str]]:
    """Resolve blob_keys to local paths via concurrent download.

    S3-native reads (Daft reading s3:// URIs directly) are deferred until
    Daft's S3 configuration supports HTTP MinIO endpoints correctly.
    All blobs are currently downloaded to tmp_dir.
    """
    local_paths = _resolve_blob_keys(blob_keys, lake, tmp_dir)
    return [], local_paths


def _resolve_blob_sources(
    blob_keys: dict[str, list[str]], lake: Any, tmp_dir: str,
) -> dict[str, list[str]]:
    resolved: dict[str, list[str]] = {}
    for modality, keys in blob_keys.items():
        s3_uris, local_paths = _resolve_blob_keys_smart(keys, lake, tmp_dir)
        combined = s3_uris + local_paths
        if combined:
            resolved[modality] = combined
    return resolved


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@router.post("/{name}/upload", response_model=UploadResponse, status_code=201)
async def upload_files(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    files: list[UploadFile] = File(..., max_length=20),
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> UploadResponse:
    """Upload files to MinIO for later ingestion (proxy mode).

    For better performance, prefer ``POST /{name}/upload/presign`` which
    returns presigned URLs for direct-to-MinIO upload.
    """
    authorize_dataset(request, name, write=True)
    blob_store = _get_blob_store(lake)
    uploaded: list[UploadedBlob] = []

    for f in files:
        # Content-Type validation
        ct = f.content_type or ""
        if ct and not ct.startswith(_ALLOWED_CONTENT_PREFIXES):
            from fastapi import HTTPException
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported Content-Type '{ct}' for file '{f.filename}'",
            )

        # Size check: reject oversized uploads before reading into memory
        content_length = f.size if hasattr(f, "size") and f.size else None
        if content_length is not None and content_length > _MAX_UPLOAD_BYTES:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=413,
                detail=f"File '{f.filename}' too large ({content_length} bytes, max {_MAX_UPLOAD_BYTES})",
            )
        data = await f.read()
        if len(data) > _MAX_UPLOAD_BYTES:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=413,
                detail=f"File '{f.filename}' too large ({len(data)} bytes, max {_MAX_UPLOAD_BYTES})",
            )
        key = _unique_blob_key(name, f.filename or "unnamed")
        result = blob_store.upload(key, data, content_type=f.content_type)
        uploaded.append(
            UploadedBlob(
                key=result.key,
                size_bytes=result.size_bytes,
                content_type=f.content_type or "",
            )
        )

    return UploadResponse(blobs=uploaded)


@router.post("/{name}/upload/presign", response_model=PresignResponse)
async def presign_upload(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: PresignRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> PresignResponse:
    """Generate presigned PUT URLs for direct-to-MinIO upload.

    Client uploads files directly to MinIO using the returned URLs,
    then calls an ingest endpoint with the returned blob keys.
    This avoids routing file data through the API server.
    """
    authorize_dataset(request, name, write=True)
    blob_store = _get_blob_store(lake)
    uploads: list[PresignedUpload] = []

    for filename in req.filenames:
        key = _unique_blob_key(name, filename)
        url = blob_store.presigned_url(key, expires_in=3600, operation="put_object")
        uploads.append(PresignedUpload(key=key, upload_url=url))

    return PresignResponse(uploads=uploads)


@router.delete("/{name}/upload/cleanup", response_model=CleanupResponse)
async def cleanup_uploads(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> CleanupResponse:
    """Delete all uploaded blobs for a dataset from MinIO.

    Call after dataset deletion to prevent orphaned uploads from accumulating.
    """
    authorize_dataset(request, name, write=True)
    blob_store = _get_blob_store(lake)
    deleted = blob_store.delete_prefix(f"uploads/{name}/")
    return CleanupResponse(deleted_count=deleted)


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


@router.post("/{name}/ingest", response_model=IngestResponse, status_code=201)
async def ingest_files(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestFilesRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_permission(Permission.DATASET_WRITE)),
) -> IngestResponse:
    """Ingest local files into a dataset."""
    authorize_dataset(request, name, write=True)
    all_paths = list(req.file_paths)
    tmp_dir: str | None = None
    try:
        if req.blob_keys:
            tmp_dir = tempfile.mkdtemp(prefix="al_ingest_")
            s3_uris, local_paths = _resolve_blob_keys_smart(req.blob_keys, lake, tmp_dir)
            all_paths.extend(s3_uris)
            all_paths.extend(local_paths)
        # Build transforms from JSON spec if provided
        transforms = None
        if req.transforms:
            from arrow_lake.ingest.transforms import build_transforms
            transforms = build_transforms(req.transforms)
        report = await run_sync(
            lake.ingest, name, all_paths,
            timeout=_INGEST_TIMEOUT, label="ingest_files", actor=actor_of(_user),
            transforms=transforms,
            executor=ingest_executor,
        )
        _after_ingest_hooks(request.app.state, name, lake)
        return IngestResponse.from_report(report)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/{name}/ingest/sql", response_model=IngestResponse, status_code=201)
async def ingest_sql(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestSqlRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_permission(Permission.DATASET_WRITE)),
) -> IngestResponse:
    """Ingest data from a SQL database query."""
    authorize_dataset(request, name, write=True)
    transforms = None
    if req.transforms:
        from arrow_lake.ingest.transforms import build_transforms
        transforms = build_transforms(req.transforms)
    report = await run_sync(
        lake.ingest_sql, name,
        sql=req.sql,
        connection_url=req.connection_url,
        partition_col=req.partition_col,
        num_partitions=req.num_partitions,
        transforms=transforms,
        timeout=_INGEST_TIMEOUT, label="ingest_sql", actor=actor_of(_user),
        executor=ingest_executor,
    )
    _after_ingest_hooks(request, name, lake)
    return IngestResponse.from_report(report)


@router.post("/{name}/ingest/kafka", response_model=IngestResponse, status_code=201)
async def ingest_kafka(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestKafkaRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_permission(Permission.DATASET_WRITE)),
) -> IngestResponse:
    """Ingest messages from Kafka topics."""
    authorize_dataset(request, name, write=True)
    transforms = None
    if req.transforms:
        from arrow_lake.ingest.transforms import build_transforms
        transforms = build_transforms(req.transforms)
    report = await run_sync(
        lake.ingest_kafka, name,
        bootstrap_servers=req.bootstrap_servers,
        topics=req.topics,
        start=req.start,
        end=req.end,
        json_decode=req.json_decode,
        transforms=transforms,
        timeout=_INGEST_TIMEOUT, label="ingest_kafka", actor=actor_of(_user),
        executor=ingest_executor,
    )
    _after_ingest_hooks(request, name, lake)
    return IngestResponse.from_report(report)


@router.post("/{name}/ingest/iceberg", response_model=IngestResponse, status_code=201)
async def ingest_iceberg(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestIcebergRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_permission(Permission.DATASET_WRITE)),
) -> IngestResponse:
    """Ingest data from an Apache Iceberg table."""
    authorize_dataset(request, name, write=True)
    transforms = None
    if req.transforms:
        from arrow_lake.ingest.transforms import build_transforms
        transforms = build_transforms(req.transforms)
    report = await run_sync(
        lake.ingest_iceberg, name,
        table_uri=req.table_uri, transforms=transforms,
        timeout=_INGEST_TIMEOUT, label="ingest_iceberg", actor=actor_of(_user),
        executor=ingest_executor,
    )
    _after_ingest_hooks(request, name, lake)
    return IngestResponse.from_report(report)


@router.post("/{name}/ingest/deltalake", response_model=IngestResponse, status_code=201)
async def ingest_deltalake(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestDeltaLakeRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_permission(Permission.DATASET_WRITE)),
) -> IngestResponse:
    """Ingest data from a Delta Lake table."""
    authorize_dataset(request, name, write=True)
    transforms = None
    if req.transforms:
        from arrow_lake.ingest.transforms import build_transforms
        transforms = build_transforms(req.transforms)
    report = await run_sync(
        lake.ingest_deltalake, name,
        table_uri=req.table_uri, version=req.version, transforms=transforms,
        timeout=_INGEST_TIMEOUT, label="ingest_deltalake", actor=actor_of(_user),
        executor=ingest_executor,
    )
    _after_ingest_hooks(request, name, lake)
    return IngestResponse.from_report(report)


@router.post("/{name}/ingest/http", response_model=IngestResponse, status_code=201)
async def ingest_http(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestHttpRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_permission(Permission.DATASET_WRITE)),
) -> IngestResponse:
    """Ingest files from HTTP(S) URLs into a dataset."""
    authorize_dataset(request, name, write=True)
    report = await run_sync(
        lake.ingest_http, name, req.urls,
        timeout=_INGEST_TIMEOUT, label="ingest_http", actor=actor_of(_user),
        executor=ingest_executor,
    )
    _after_ingest_hooks(request, name, lake)
    return IngestResponse.from_report(report)


@router.post("/{name}/ingest/images", response_model=IngestResponse, status_code=201)
async def ingest_images(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestImagesRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_permission(Permission.DATASET_WRITE)),
) -> IngestResponse:
    """Ingest image files with thumbnails and EXIF metadata."""
    authorize_dataset(request, name, write=True)
    all_paths = list(req.file_paths)
    tmp_dir: str | None = None
    try:
        if req.blob_keys:
            tmp_dir = tempfile.mkdtemp(prefix="al_ingest_")
            # Images always need local paths (Pillow requirement)
            all_paths.extend(_resolve_blob_keys(req.blob_keys, lake, tmp_dir))
        report = await run_sync(
            lake.ingest_images, name, all_paths,
            timeout=_INGEST_TIMEOUT, label="ingest_images", actor=actor_of(_user),
            executor=ingest_executor,
        )
        # 自动 CLIP embed(图像语义检索;模型不可用 → 静默跳过,不阻塞摄入,图已落库可后续手动 embed)
        try:
            await run_sync(lake.embed_media, name, image_column="image_data", timeout=_INGEST_TIMEOUT, label="embed_images", executor=ingest_executor)
        except Exception:
            pass
        _after_ingest_hooks(request.app.state, name, lake)
        return IngestResponse.from_report(report)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/{name}/ingest/videos", response_model=IngestResponse, status_code=201)
async def ingest_videos(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestVideosRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_permission(Permission.DATASET_WRITE)),
) -> IngestResponse:
    """Ingest video files with keyframe extraction."""
    authorize_dataset(request, name, write=True)
    all_paths = list(req.file_paths)
    tmp_dir: str | None = None
    try:
        if req.blob_keys:
            tmp_dir = tempfile.mkdtemp(prefix="al_ingest_")
            # Videos always need local paths (av/ffmpeg requirement)
            all_paths.extend(_resolve_blob_keys(req.blob_keys, lake, tmp_dir))
        report = await run_sync(
            lake.ingest_videos, name, all_paths,
            timeout=_INGEST_TIMEOUT, label="ingest_videos", actor=actor_of(_user),
            executor=ingest_executor,
        )
        # 自动 CLIP embed 关键帧(视频语义检索;模型不可用 → 静默跳过)
        try:
            await run_sync(lake.embed_media, name, image_column="video_data", timeout=_INGEST_TIMEOUT, label="embed_videos", executor=ingest_executor)
        except Exception:
            pass
        _after_ingest_hooks(request.app.state, name, lake)
        return IngestResponse.from_report(report)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/{name}/ingest/mixed", response_model=IngestResponse, status_code=201)
async def ingest_mixed(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestMixedRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_permission(Permission.DATASET_WRITE)),
) -> IngestResponse:
    """Ingest mixed-modality sources (files, URLs, images, videos)."""
    authorize_dataset(request, name, write=True)
    sources = dict(req.sources)
    tmp_dir: str | None = None
    try:
        if req.blob_keys:
            tmp_dir = tempfile.mkdtemp(prefix="al_ingest_")
            resolved = _resolve_blob_sources(req.blob_keys, lake, tmp_dir)
            for modality, paths in resolved.items():
                sources.setdefault(modality, []).extend(paths)
        report = await run_sync(
            lake.ingest_mixed, name, sources,
            timeout=_INGEST_TIMEOUT, label="ingest_mixed", actor=actor_of(_user),
            executor=ingest_executor,
        )
        _after_ingest_hooks(request.app.state, name, lake)
        return IngestResponse.from_report(report)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/{name}/ingest/documents", response_model=IngestResponse, status_code=201)
async def ingest_documents(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestDocumentsRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_permission(Permission.DATASET_WRITE)),
) -> IngestResponse:
    """Ingest documents: parse → chunk → embed → store.

    Accepts any format the parser (kreuzberg) supports — PDF, markdown, plain
    text, HTML, Office docs (see ``_DOCUMENT_EXTENSIONS``).
    """
    authorize_dataset(request, name, write=True)
    all_paths = list(req.pdf_paths)
    tmp_dir: str | None = None
    try:
        if req.blob_keys:
            tmp_dir = tempfile.mkdtemp(prefix="al_ingest_")
            # Documents always need local paths (parser requirement)
            all_paths.extend(_resolve_blob_keys(req.blob_keys, lake, tmp_dir))
        doc_config = lake._config.document if hasattr(lake, "_config") else None
        # parse→store→embed→FTS→vector consolidated in the facade (架构评审 #4);
        # each post-step is best-effort there. _after_ingest_hooks (gravitino +
        # cache invalidate) stays here — app_state-scoped, HTTP-layer concern.
        # timeout covers the full ingest+embed+FTS+vector sequence (4 steps);
        # previously each step had its own _INGEST_TIMEOUT — keep equivalent budget.
        report = await run_sync(
            lake.ingest_documents_and_index, name, all_paths, doc_config=doc_config,
            doc_type=req.doc_type, timeout=_INGEST_TIMEOUT * 4, label="ingest_documents",
            actor=actor_of(_user),
            executor=ingest_executor,
        )
        _after_ingest_hooks(request.app.state, name, lake)
        return IngestResponse.from_report(report)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/{name}/embed/status")
async def get_embed_backfill_status(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
):
    """v1.10.2 P1.4: background embed+vector backfill status for a dataset.

    Returns the latest deferred-embedding status (running/completed/failed) or
    ``{"status": "idle"}`` when no backfill has ever run. Vector search is
    unavailable while ``status == "running"``; FTS works throughout.
    """
    authorize_dataset(request, name)  # dataset-level ACL (read)
    status = lake.get_embed_backfill_status(name)
    if status is None:
        return {"status": "idle"}
    return status


# ---------------------------------------------------------------------------
# Dataset CRUD
# ---------------------------------------------------------------------------


# —— Dataset description(轻量 JSON store;本地可信原型,生产化迁 system_db)——
_DESC_PATH = os.path.join(
    os.environ.get("ARROW_LAKE__LAKE__DATA_DIR", "/data/lake"),
    ".console", "dataset_descriptions.json",
)


def _read_desc_map() -> dict[str, str]:
    import json
    try:
        with open(_DESC_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_desc(name: str, description: str) -> None:
    import fcntl
    import json
    m = _read_desc_map()
    m[name] = (description or "").strip()
    try:
        os.makedirs(os.path.dirname(_DESC_PATH), exist_ok=True)
        with open(_DESC_PATH, "w", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            json.dump(m, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass  # 只读 FS → 静默(不阻塞摄入)


@router.put("/{name}/description", response_model=MessageResponse, summary="Set dataset description (console)")
async def set_dataset_description(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: DatasetDescriptionRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> MessageResponse:
    """Set/update a human-readable description for a dataset (local JSON store)."""
    authorize_dataset(request, name, write=True)
    # 验证数据集存在(防给不存在的数据集写描述污染 store)
    result = await run_sync(lake.catalog, timeout=_ADMIN_TIMEOUT, label="catalog")
    if not any(e.name == name for e in result.datasets):
        raise CatalogError(
            error_code=ErrorCode.CATALOG_DATASET_NOT_FOUND,
            message=f"Dataset '{name}' not found",
        )
    _save_desc(name, req.description)
    return MessageResponse(message=f"description updated for {name}")


@router.get("", response_model=DatasetListResponse)
async def list_datasets(
    _auth: None = Depends(require_role(Role.VIEWER)),
    lake=Depends(get_lake),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> DatasetListResponse:
    """List all datasets with metadata. Supports pagination via limit/offset.

    System tables (``_``-prefixed, e.g. ``_audit_trail`` / ``_lineage_events``)
    are visible to ADMIN only; other roles get them filtered out.
    """
    result = await run_sync(lake.catalog, timeout=_ADMIN_TIMEOUT, label="catalog")
    # require_role 返回当前 user(TokenPayload);系统表(_ 前缀)仅 admin 可见
    is_admin = _auth is not None and getattr(_auth, "role", None) == Role.ADMIN
    visible = [e for e in result.datasets if is_admin or not is_internal_table(e.name)]
    # 一次扫描 KA base 得到已构建 KG 的数据集集合(避免前端 N 次 /kg/stats)
    from pathlib import Path
    from arrow_lake.knowledge_graph._naming import artifact_key_for
    ka_base = getattr(lake.config.hugegraph, "he_ka_base_dir", None)
    ka_keys: set[str] = set()
    if ka_base and Path(ka_base).is_dir():
        ka_keys = {
            d.name for d in Path(ka_base).iterdir()
            if d.is_dir() and (d / "ka" / "data.json").is_file()
        }
    desc_map = _read_desc_map()
    all_datasets = [
        DatasetInfo(
            name=e.name, version=e.version, num_rows=e.num_rows,
            num_columns=e.num_columns, vector_dim=e.vector_dim,
            has_vector_index=e.has_vector_index, has_fts_index=e.has_fts_index,
            has_kg=artifact_key_for(e.name) in ka_keys,
            size_bytes=e.size_bytes, created_at=e.created_at, updated_at=e.updated_at,
            description=desc_map.get(e.name),
            kind=getattr(e, "kind", "structured"),
        )
        for e in visible
    ]
    page = all_datasets[offset : offset + limit]
    return DatasetListResponse(datasets=page, total=len(visible))


def _dataset_has_kg(lake: Any, name: str) -> bool:
    """Return whether a dataset has a built KG (KA dump on disk).

    O(1) file check — lets callers learn whether a KG exists without hitting
    HugeGraph (``/kg/stats`` on a KG-less dataset is an empty-graph query).
    Mirrors the ``ka_keys`` logic in ``list_datasets`` but for a single dataset.
    """
    from pathlib import Path

    from arrow_lake.knowledge_graph._naming import artifact_key_for

    ka_base = getattr(lake.config.hugegraph, "he_ka_base_dir", None)
    if not isinstance(ka_base, (str, Path)) or not ka_base:
        return False
    try:
        return (Path(ka_base) / artifact_key_for(name) / "ka" / "data.json").is_file()
    except (OSError, TypeError):
        return False


@router.get("/{name}", response_model=DatasetInfo)
async def get_dataset(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    _auth: None = Depends(require_role(Role.VIEWER)),
    _acl_guard: None = Depends(authorize_dataset_read),
    lake=Depends(get_lake),
) -> DatasetInfo:
    """Get metadata for a specific dataset."""
    result = await run_sync(lake.catalog, timeout=_ADMIN_TIMEOUT, label="catalog")
    for entry in result.datasets:
        if entry.name == name:
            kind = getattr(entry, "kind", "structured")
            tables: list[str] | None = None
            if kind == "container":
                # W4.2: container detail carries the table list (each table's
                # schema/rows go through the ?table= endpoints).
                def _tables() -> list[str]:
                    got = lake._get_storage().list_container_tables(name)
                    return list(got) if isinstance(got, (list, tuple)) else []

                tables = await run_sync(_tables, timeout=_ADMIN_TIMEOUT, label="tables")
            return DatasetInfo(
                name=entry.name,
                version=entry.version,
                num_rows=entry.num_rows,
                num_columns=entry.num_columns,
                vector_dim=entry.vector_dim,
                has_vector_index=entry.has_vector_index,
                has_fts_index=entry.has_fts_index,
                has_kg=_dataset_has_kg(lake, entry.name),
                size_bytes=entry.size_bytes,
                created_at=entry.created_at,
                updated_at=entry.updated_at,
                description=_read_desc_map().get(entry.name),
                kind=kind,
                tables=tables,
            )
    raise CatalogError(
        error_code=ErrorCode.CATALOG_DATASET_NOT_FOUND,
        message=f"Dataset '{name}' not found",
    )


@router.get("/{name}/schema", response_model=SchemaResponse)
async def get_dataset_schema(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    table: str | None = Query(None, pattern=_TABLE_NAME_PATTERN),
    _auth: None = Depends(require_role(Role.VIEWER)),
    _acl_guard: None = Depends(authorize_dataset_read),
    lake=Depends(get_lake),
) -> SchemaResponse:
    """Return the dataset's authoritative field schema (name + Arrow type).

    Unlike inferring columns from a preview row, this reads the Lance schema
    directly — so it's correct for empty datasets and carries field types.
    Field comments (stored in Arrow field metadata) are included. ``table``
    addresses a table inside a container dataset (DR14 W4.2); a bare
    container name is rejected with 422 (D6 semantics, mirroring OLAP).
    """
    if table is None:
        def _probe() -> list[str]:
            got = lake._get_storage().list_container_tables(name)
            return list(got) if isinstance(got, (list, tuple)) else []

        tables = await run_sync(_probe, timeout=_ADMIN_TIMEOUT, label="tables")
        if tables:
            raise QueryError(
                error_code=ErrorCode.OLAP_AMBIGUOUS_DATASET,
                message=(
                    f"Dataset '{name}' is a multi-table container — "
                    f"pass ?table=<name> (available: {', '.join(tables)})"
                ),
            )

    def _read() -> list[dict[str, Any]]:
        schema = lake.open_dataset(name, table=table).schema
        return _schema_field_dicts(schema)

    fields = await run_sync(_read, timeout=_ADMIN_TIMEOUT, label="schema")
    return SchemaResponse(name=name, fields=[SchemaField(**f) for f in fields])


@router.post("/{name}/schema/annotate", response_model=SchemaResponse)
async def annotate_schema(
    name: str = Path(..., pattern=_NAME_PATTERN),
    body: SchemaAnnotateRequest = ...,
    *,
    _auth: None = Depends(require_role(Role.ADMIN)),
    lake=Depends(get_lake),
) -> SchemaResponse:
    """Set or clear a field's human-readable comment (Arrow field metadata).

    Persists via Lance ``update_field_metadata`` (no data rewrite). Returns the
    refreshed schema so the console can re-render without a second request.
    """
    def _apply() -> list[dict[str, Any]]:
        schema = lake.open_dataset(name).schema
        if body.field not in schema.names:
            raise CatalogError(
                error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
                message=f"Field '{body.field}' not found in dataset '{name}'",
            )
        lake.update_field_comments(name, {body.field: body.comment})
        return _schema_field_dicts(lake.open_dataset(name).schema)

    fields = await run_sync(_apply, timeout=_ADMIN_TIMEOUT, label="schema_annotate")
    return SchemaResponse(name=name, fields=[SchemaField(**f) for f in fields])


@router.post("/{name}/schema/migrate", response_model=SchemaMigrationResponse)
async def migrate_schema(
    name: str = Path(..., pattern=_NAME_PATTERN),
    body: SchemaMigrationRequest = ...,
    *,
    table: str | None = Query(None, pattern=_TABLE_NAME_PATTERN),
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> SchemaMigrationResponse:
    """Validate and optionally apply schema migration actions.

    With ``dry_run=true`` (default), only validates compatibility.
    Set ``dry_run=false`` to apply the migration.
    ``?table=`` targets a table inside a container dataset (DR14).
    """
    import pyarrow as pa

    from arrow_lake.ingest.schema import SchemaCompatibilityChecker, SchemaMigrationError

    # Get current schema
    catalog = await run_sync(lake.catalog, timeout=_ADMIN_TIMEOUT, label="catalog")
    dataset_entry = None
    for entry in catalog.datasets:
        if entry.name == name:
            dataset_entry = entry
            break
    if dataset_entry is None:
        raise CatalogError(
            error_code=ErrorCode.CATALOG_DATASET_NOT_FOUND,
            message=f"Dataset '{name}' not found",
        )

    # Read current schema
    ds = lake._storage.open_dataset(name, table=table)
    current_schema = ds.schema
    checker = SchemaCompatibilityChecker(current_schema)

    all_issues: list[SchemaMigrationIssue] = []

    # Type mapping for alter_column
    _TYPE_MAP: dict[str, pa.DataType] = {
        "int8": pa.int8(), "int16": pa.int16(), "int32": pa.int32(), "int64": pa.int64(),
        "float32": pa.float32(), "float64": pa.float64(),
        "string": pa.string(), "binary": pa.binary(), "bool": pa.bool_(),
    }

    for i, action in enumerate(body.actions):
        issues: list[str] = []
        if action.operation == "add_column":
            col_type = _TYPE_MAP.get(action.new_type, pa.string())
            issues = checker.check_add_column(action.column_name, col_type)
        elif action.operation == "alter_column":
            new_type = _TYPE_MAP.get(action.new_type)
            if new_type is None:
                issues = [f"Unknown type '{action.new_type}'"]
            else:
                issues = checker.check_alter_column(action.column_name, new_type)
        elif action.operation == "drop_column":
            issues = checker.check_drop_column(action.column_name)
        else:
            issues = [f"Unknown operation '{action.operation}'"]

        if issues:
            all_issues.append(SchemaMigrationIssue(
                action_index=i,
                column_name=action.column_name,
                messages=issues,
            ))

    if all_issues:
        return SchemaMigrationResponse(
            success=False,
            dry_run=body.dry_run,
            issues=all_issues,
            applied_count=0,
        )

    if body.dry_run:
        return SchemaMigrationResponse(
            success=True,
            dry_run=True,
            issues=[],
            applied_count=0,
        )

    # Apply migration
    applied = 0
    for action in body.actions:
        try:
            if action.operation == "add_column":
                await run_sync(
                    lake.add_column,
                    name, action.column_name, action.sql_expr,
                    timeout=_ADMIN_TIMEOUT, label="add_column", table=table,
                )
            elif action.operation == "alter_column":
                new_type = _TYPE_MAP[action.new_type]
                await run_sync(
                    lake.alter_column,
                    name, action.column_name, new_type,
                    timeout=_ADMIN_TIMEOUT, label="alter_column", table=table,
                )
            elif action.operation == "drop_column":
                await run_sync(
                    lake.drop_column,
                    name, action.column_name,
                    timeout=_ADMIN_TIMEOUT, label="drop_column", table=table,
                )
            applied += 1
        except SchemaMigrationError:
            break

    return SchemaMigrationResponse(
        success=True,
        dry_run=False,
        issues=[],
        applied_count=applied,
    )


@router.delete("/{name}", response_model=MessageResponse, status_code=200)
async def delete_dataset(
    request: Request,
    name: str = Path(..., pattern=_NAME_PATTERN),
    cascade: bool = Query(
        True, description="Also reclaim derived assets (KG graph, KA dump, "
        "Gravitino/catalog metadata, RBAC grants, template bindings)."
    ),
    table: str | None = Query(
        None, pattern=_TABLE_NAME_PATTERN,
        description="Drop ONLY this container table (siblings untouched).",
    ),
    *,
    lake=Depends(get_lake),
    _user: dict = Depends(require_permission(Permission.DATASET_DELETE)),
) -> MessageResponse:
    """Delete a dataset and all its data (or one container table)."""
    authorize_dataset(request, name, write=True)
    # 系统运行表(sys_ 前缀)是系统运行依赖,禁止删除。判断集中 _system_tables.py。
    if is_system_table(name):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail=f"系统表 '{name}' 受保护,不可删除(系统运行依赖)",
        )
    from arrow_lake.api._security_log import actor_of
    await run_sync(
        lake.delete_dataset, name, timeout=_ADMIN_TIMEOUT,
        label="delete_dataset", actor=actor_of(_user), cascade=cascade,
        table=table,
    )
    if table is not None:
        return MessageResponse(message=f"Table '{name}/{table}' deleted")
    return MessageResponse(message=f"Dataset '{name}' deleted")
