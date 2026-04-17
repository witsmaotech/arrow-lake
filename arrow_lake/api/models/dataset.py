"""Dataset management request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

class IngestFilesRequest(BaseModel):
    """Request body for local file ingestion."""

    file_paths: list[str] = Field(..., min_length=1, description="Local file paths to ingest")

    @field_validator("file_paths")
    @classmethod
    def validate_no_traversal(cls, paths: list[str]) -> list[str]:
        for p in paths:
            if ".." in p:
                raise ValueError(f"Path traversal not allowed: {p!r}")
        return paths


class IngestHttpRequest(BaseModel):
    """Request body for HTTP URL ingestion."""

    urls: list[str] = Field(..., min_length=1, description="HTTP(S) URLs to ingest")


class IngestionSourceResponse(BaseModel):
    """Stats for a single ingestion source."""

    path: str
    row_count: int
    file_count: int = 1


class IngestResponse(BaseModel):
    """Response for successful ingestion."""

    success: bool = True
    total_rows: int = 0
    total_files: int = 0
    sources: list[IngestionSourceResponse] = Field(default_factory=list)

    @classmethod
    def from_report(cls, report: Any) -> IngestResponse:
        """Build from an IngestionReport dataclass."""
        sources = [
            IngestionSourceResponse(
                path=s.path,
                row_count=s.row_count,
                file_count=s.file_count,
            )
            for s in report.sources
        ]
        return cls(
            total_rows=report.total_rows,
            total_files=report.total_files,
            sources=sources,
        )


# ---------------------------------------------------------------------------
# Multi-modality ingest
# ---------------------------------------------------------------------------


class IngestImagesRequest(BaseModel):
    """Request body for image file ingestion."""

    file_paths: list[str] = Field(..., min_length=1, description="Image file paths to ingest")

    @field_validator("file_paths")
    @classmethod
    def validate_no_traversal(cls, paths: list[str]) -> list[str]:
        for p in paths:
            if ".." in p:
                raise ValueError(f"Path traversal not allowed: {p!r}")
        return paths


class IngestVideosRequest(BaseModel):
    """Request body for video file ingestion."""

    file_paths: list[str] = Field(..., min_length=1, description="Video file paths to ingest")

    @field_validator("file_paths")
    @classmethod
    def validate_no_traversal(cls, paths: list[str]) -> list[str]:
        for p in paths:
            if ".." in p:
                raise ValueError(f"Path traversal not allowed: {p!r}")
        return paths


class IngestMixedRequest(BaseModel):
    """Request body for mixed-modality ingestion."""

    sources: dict[str, list[str]] = Field(
        ...,
        description="Mapping of modality to paths/URLs. Keys: files, urls, images, videos.",
    )


# ---------------------------------------------------------------------------
# Dataset info
# ---------------------------------------------------------------------------

class DatasetInfo(BaseModel):
    """Metadata for a single dataset."""

    name: str
    version: int = 0
    num_rows: int = 0


class DatasetListResponse(BaseModel):
    """Response for listing all datasets."""

    success: bool = True
    datasets: list[DatasetInfo] = Field(default_factory=list)
    total: int = 0
