"""Dataset management request/response models."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

# Private IP ranges for SSRF prevention (shared with connectors_http.py).
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

class IngestFilesRequest(BaseModel):
    """Request body for local file ingestion."""

    file_paths: list[str] = Field(..., min_length=1, max_length=100, description="Local file paths to ingest")

    @field_validator("file_paths")
    @classmethod
    def validate_no_traversal(cls, paths: list[str]) -> list[str]:
        for p in paths:
            if ".." in p or "\0" in p:
                raise ValueError(f"Path traversal not allowed: {p!r}")
        return paths


class IngestHttpRequest(BaseModel):
    """Request body for HTTP URL ingestion."""

    urls: list[str] = Field(..., min_length=1, max_length=100, description="HTTP(S) URLs to ingest")

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, urls: list[str]) -> list[str]:
        for url in urls:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                raise ValueError(f"URL must use http/https scheme: {url!r}")
            hostname = parsed.hostname
            if not hostname:
                raise ValueError(f"URL must include a hostname: {url!r}")
            try:
                addr = ipaddress.ip_address(hostname)
                if any(addr in net for net in _PRIVATE_NETWORKS):
                    raise ValueError(f"URL hostname resolves to a private IP: {hostname}")
            except ValueError:
                pass  # domain name, not an IP
        return urls


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

    file_paths: list[str] = Field(..., min_length=1, max_length=100, description="Image file paths to ingest")

    @field_validator("file_paths")
    @classmethod
    def validate_no_traversal(cls, paths: list[str]) -> list[str]:
        for p in paths:
            if ".." in p or "\0" in p:
                raise ValueError(f"Path traversal not allowed: {p!r}")
        return paths


class IngestVideosRequest(BaseModel):
    """Request body for video file ingestion."""

    file_paths: list[str] = Field(..., min_length=1, max_length=100, description="Video file paths to ingest")

    @field_validator("file_paths")
    @classmethod
    def validate_no_traversal(cls, paths: list[str]) -> list[str]:
        for p in paths:
            if ".." in p or "\0" in p:
                raise ValueError(f"Path traversal not allowed: {p!r}")
        return paths


class IngestMixedRequest(BaseModel):
    """Request body for mixed-modality ingestion."""

    sources: dict[str, list[str]] = Field(
        ...,
        description="Mapping of modality to paths/URLs. Keys: files, urls, images, videos.",
    )

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        allowed_keys = {"files", "urls", "images", "videos"}
        for key, paths in v.items():
            if key not in allowed_keys:
                raise ValueError(f"Unknown modality key: {key!r}. Allowed: {allowed_keys}")
            if len(paths) > 100:
                raise ValueError(f"Too many paths for {key!r}: max 100")
            for p in paths:
                if ".." in p or "\0" in p:
                    raise ValueError(f"Path traversal not allowed: {p!r}")
                if p.startswith("/"):
                    raise ValueError(f"Absolute paths not allowed: {p!r}")
        return v


class IngestDocumentsRequest(BaseModel):
    """Request body for PDF document ingestion."""

    pdf_paths: list[str] = Field(..., min_length=1, max_length=100, description="PDF file paths to ingest")

    @field_validator("pdf_paths")
    @classmethod
    def validate_pdf_paths(cls, paths: list[str]) -> list[str]:
        for p in paths:
            if ".." in p or "\0" in p:
                raise ValueError(f"Path traversal not allowed: {p!r}")
            if p.startswith("/"):
                raise ValueError(f"Absolute paths not allowed: {p!r}")
            if not p.lower().endswith(".pdf"):
                raise ValueError(f"Not a PDF file: {p!r}")
        return paths


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
