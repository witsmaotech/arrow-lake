"""Dataset management and ingestion endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.models.common import _NAME_PATTERN, MessageResponse
from arrow_lake.api.models.dataset import (
    DatasetInfo,
    DatasetListResponse,
    IngestDocumentsRequest,
    IngestFilesRequest,
    IngestHttpRequest,
    IngestImagesRequest,
    IngestMixedRequest,
    IngestResponse,
    IngestVideosRequest,
)
from arrow_lake.exceptions import CatalogError, ErrorCode

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])


@router.post("/{name}/ingest", response_model=IngestResponse, status_code=201)
async def ingest_files(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestFilesRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest local files into a dataset."""
    report = lake.ingest(name, req.file_paths)
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
    report = lake.ingest_http(name, req.urls)
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
    report = lake.ingest_images(name, req.file_paths)
    return IngestResponse.from_report(report)


@router.post("/{name}/ingest/videos", response_model=IngestResponse, status_code=201)
async def ingest_videos(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestVideosRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest video files with keyframe extraction."""
    report = lake.ingest_videos(name, req.file_paths)
    return IngestResponse.from_report(report)


@router.post("/{name}/ingest/mixed", response_model=IngestResponse, status_code=201)
async def ingest_mixed(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestMixedRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest mixed-modality sources (files, URLs, images, videos)."""
    report = lake.ingest_mixed(name, req.sources)
    return IngestResponse.from_report(report)


@router.post("/{name}/ingest/documents", response_model=IngestResponse, status_code=201)
async def ingest_documents(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: IngestDocumentsRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> IngestResponse:
    """Ingest PDF documents: parse → chunk → embed → store."""
    doc_config = lake._config.document if hasattr(lake, '_config') else None
    report = lake.ingest_documents(name, req.pdf_paths, doc_config=doc_config)
    return IngestResponse.from_report(report)


@router.get("", response_model=DatasetListResponse)
async def list_datasets(
    lake=Depends(get_lake),
) -> DatasetListResponse:
    """List all datasets with metadata."""
    result = lake.catalog()
    datasets = [
        DatasetInfo(name=e.name, version=e.version, num_rows=e.num_rows)
        for e in result.datasets
    ]
    return DatasetListResponse(datasets=datasets, total=result.total)


@router.get("/{name}", response_model=DatasetInfo)
async def get_dataset(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    lake=Depends(get_lake),
) -> DatasetInfo:
    """Get metadata for a specific dataset."""
    result = lake.catalog()
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
    lake.delete_dataset(name)
    return MessageResponse(message=f"Dataset '{name}' deleted")
