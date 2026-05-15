"""Dataset management and ingestion endpoints."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Depends, File, Path, Query, UploadFile

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.models.common import _NAME_PATTERN, MessageResponse
from arrow_lake.api.models.dataset import (
    CleanupResponse,
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
    PresignedUpload,
    PresignRequest,
    PresignResponse,
    UploadedBlob,
    UploadResponse,
)
from arrow_lake.api.utils import run_sync
from arrow_lake.exceptions import CatalogError, ErrorCode

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])

_INGEST_TIMEOUT = 600
_ADMIN_TIMEOUT = 60
_DOWNLOAD_WORKERS = 4
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
_ALLOWED_CONTENT_PREFIXES = (
    "text/", "application/", "image/", "video/", "audio/",
    "multipart/",
)

_SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\-.][a-zA-Z0-9_\-.]*$")

# Extensions that Daft can read directly from S3 URIs.
_S3_NATIVE_EXTENSIONS = frozenset({".csv", ".json", ".jsonl", ".parquet"})


def _sanitize_filename(name: str) -> str:
    from arrow_lake.api.models.dataset import _check_no_traversal

    stripped = os.path.basename(name)
    _check_no_traversal(stripped)
    if not stripped or not _SAFE_FILENAME_RE.match(stripped):
        raise ValueError(f"Invalid filename: {name!r}")
    return stripped


def _unique_blob_key(dataset_name: str, filename: str) -> str:
    """Build a collision-resistant blob key: uploads/{ds}/{uuid8}_{filename}."""
    safe = _sanitize_filename(filename)
    prefix = uuid.uuid4().hex[:8]
    return f"uploads/{dataset_name}/{prefix}_{safe}"


def _get_blob_store(lake: Any) -> Any:
    from arrow_lake.storage.blob_store import BlobStoreManager

    return lake._get_component(
        "blob_store",
        lambda: BlobStoreManager(config=lake._config.storage),
    )


def _blob_key_to_s3_uri(key: str, lake: Any) -> str:
    sc = lake._config.storage
    return f"s3://{sc.s3_bucket}/{key}"


def _is_s3_native(key: str) -> bool:
    ext = os.path.splitext(key)[1].lower()
    return ext in _S3_NATIVE_EXTENSIONS


def _resolve_blob_keys(blob_keys: list[str], lake: Any, tmp_dir: str) -> list[str]:
    blob_store = _get_blob_store(lake)

    def _download_one(idx_key: tuple[int, str]) -> str:
        idx, key = idx_key
        filename = key.rsplit("/", 1)[-1]
        # Use index prefix to prevent filename collisions
        dest = os.path.join(tmp_dir, f"{idx:04d}_{filename}")
        blob_store.download_file(key, dest)
        if not os.path.exists(dest) or os.path.getsize(dest) == 0:
            raise OSError(f"Download verification failed for blob key: {key}")
        return dest

    try:
        with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as pool:
            return list(pool.map(_download_one, enumerate(blob_keys)))
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
    name: str = Path(..., pattern=_NAME_PATTERN),
    files: list[UploadFile] = File(..., max_length=20),
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> UploadResponse:
    """Upload files to MinIO for later ingestion (proxy mode).

    For better performance, prefer ``POST /{name}/upload/presign`` which
    returns presigned URLs for direct-to-MinIO upload.
    """
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
    blob_store = _get_blob_store(lake)
    uploads: list[PresignedUpload] = []

    for filename in req.filenames:
        key = _unique_blob_key(name, filename)
        url = blob_store.presigned_url(key, expires_in=3600, operation="put_object")
        uploads.append(PresignedUpload(key=key, upload_url=url))

    return PresignResponse(uploads=uploads)


@router.delete("/{name}/upload/cleanup", response_model=CleanupResponse)
async def cleanup_uploads(
    name: str = Path(..., pattern=_NAME_PATTERN),
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> CleanupResponse:
    """Delete all uploaded blobs for a dataset from MinIO.

    Call after dataset deletion to prevent orphaned uploads from accumulating.
    """
    blob_store = _get_blob_store(lake)
    deleted = blob_store.delete_prefix(f"uploads/{name}/")
    return CleanupResponse(deleted_count=deleted)


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


@router.post("/{name}/ingest", response_model=IngestResponse, status_code=201)
async def ingest_files(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestFilesRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest local files into a dataset."""
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
            timeout=_INGEST_TIMEOUT, label="ingest_files",
            transforms=transforms,
        )
        return IngestResponse.from_report(report)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/{name}/ingest/sql", response_model=IngestResponse, status_code=201)
async def ingest_sql(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestSqlRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest data from a SQL database query."""
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
        timeout=_INGEST_TIMEOUT, label="ingest_sql",
    )
    return IngestResponse.from_report(report)


@router.post("/{name}/ingest/kafka", response_model=IngestResponse, status_code=201)
async def ingest_kafka(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestKafkaRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest messages from Kafka topics."""
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
        timeout=_INGEST_TIMEOUT, label="ingest_kafka",
    )
    return IngestResponse.from_report(report)


@router.post("/{name}/ingest/iceberg", response_model=IngestResponse, status_code=201)
async def ingest_iceberg(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestIcebergRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest data from an Apache Iceberg table."""
    transforms = None
    if req.transforms:
        from arrow_lake.ingest.transforms import build_transforms
        transforms = build_transforms(req.transforms)
    report = await run_sync(
        lake.ingest_iceberg, name,
        table_uri=req.table_uri, transforms=transforms,
        timeout=_INGEST_TIMEOUT, label="ingest_iceberg",
    )
    return IngestResponse.from_report(report)


@router.post("/{name}/ingest/deltalake", response_model=IngestResponse, status_code=201)
async def ingest_deltalake(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestDeltaLakeRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest data from a Delta Lake table."""
    transforms = None
    if req.transforms:
        from arrow_lake.ingest.transforms import build_transforms
        transforms = build_transforms(req.transforms)
    report = await run_sync(
        lake.ingest_deltalake, name,
        table_uri=req.table_uri, version=req.version, transforms=transforms,
        timeout=_INGEST_TIMEOUT, label="ingest_deltalake",
    )
    return IngestResponse.from_report(report)


@router.post("/{name}/ingest/http", response_model=IngestResponse, status_code=201)
async def ingest_http(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestHttpRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest files from HTTP(S) URLs into a dataset."""
    report = await run_sync(
        lake.ingest_http, name, req.urls,
        timeout=_INGEST_TIMEOUT, label="ingest_http",
    )
    return IngestResponse.from_report(report)


@router.post("/{name}/ingest/images", response_model=IngestResponse, status_code=201)
async def ingest_images(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestImagesRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest image files with thumbnails and EXIF metadata."""
    all_paths = list(req.file_paths)
    tmp_dir: str | None = None
    try:
        if req.blob_keys:
            tmp_dir = tempfile.mkdtemp(prefix="al_ingest_")
            # Images always need local paths (Pillow requirement)
            all_paths.extend(_resolve_blob_keys(req.blob_keys, lake, tmp_dir))
        report = await run_sync(
            lake.ingest_images, name, all_paths,
            timeout=_INGEST_TIMEOUT, label="ingest_images",
        )
        return IngestResponse.from_report(report)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/{name}/ingest/videos", response_model=IngestResponse, status_code=201)
async def ingest_videos(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestVideosRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest video files with keyframe extraction."""
    all_paths = list(req.file_paths)
    tmp_dir: str | None = None
    try:
        if req.blob_keys:
            tmp_dir = tempfile.mkdtemp(prefix="al_ingest_")
            # Videos always need local paths (av/ffmpeg requirement)
            all_paths.extend(_resolve_blob_keys(req.blob_keys, lake, tmp_dir))
        report = await run_sync(
            lake.ingest_videos, name, all_paths,
            timeout=_INGEST_TIMEOUT, label="ingest_videos",
        )
        return IngestResponse.from_report(report)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/{name}/ingest/mixed", response_model=IngestResponse, status_code=201)
async def ingest_mixed(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestMixedRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest mixed-modality sources (files, URLs, images, videos)."""
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
            timeout=_INGEST_TIMEOUT, label="ingest_mixed",
        )
        return IngestResponse.from_report(report)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/{name}/ingest/documents", response_model=IngestResponse, status_code=201)
async def ingest_documents(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestDocumentsRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest PDF documents: parse → chunk → embed → store."""
    all_paths = list(req.pdf_paths)
    tmp_dir: str | None = None
    try:
        if req.blob_keys:
            tmp_dir = tempfile.mkdtemp(prefix="al_ingest_")
            # Documents always need local paths (parser requirement)
            all_paths.extend(_resolve_blob_keys(req.blob_keys, lake, tmp_dir))
        doc_config = lake._config.document if hasattr(lake, "_config") else None
        report = await run_sync(
            lake.ingest_documents, name, all_paths, doc_config=doc_config,
            timeout=_INGEST_TIMEOUT, label="ingest_documents",
        )
        return IngestResponse.from_report(report)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Dataset CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=DatasetListResponse)
async def list_datasets(
    _auth: None = Depends(require_role(Role.VIEWER)),
    lake=Depends(get_lake),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> DatasetListResponse:
    """List all datasets with metadata. Supports pagination via limit/offset."""
    result = await run_sync(lake.catalog, timeout=_ADMIN_TIMEOUT, label="catalog")
    all_datasets = [
        DatasetInfo(name=e.name, version=e.version, num_rows=e.num_rows)
        for e in result.datasets
    ]
    page = all_datasets[offset : offset + limit]
    return DatasetListResponse(datasets=page, total=result.total)


@router.get("/{name}", response_model=DatasetInfo)
async def get_dataset(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    _auth: None = Depends(require_role(Role.VIEWER)),
    lake=Depends(get_lake),
) -> DatasetInfo:
    """Get metadata for a specific dataset."""
    result = await run_sync(lake.catalog, timeout=_ADMIN_TIMEOUT, label="catalog")
    for entry in result.datasets:
        if entry.name == name:
            return DatasetInfo(
                name=entry.name,
                version=entry.version,
                num_rows=entry.num_rows,
            )
    raise CatalogError(
        error_code=ErrorCode.CATALOG_DATASET_NOT_FOUND,
        message=f"Dataset '{name}' not found",
    )


@router.delete("/{name}", response_model=MessageResponse, status_code=200)
async def delete_dataset(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> MessageResponse:
    """Delete a dataset and all its data."""
    await run_sync(lake.delete_dataset, name, timeout=_ADMIN_TIMEOUT, label="delete_dataset")
    return MessageResponse(message=f"Dataset '{name}' deleted")
