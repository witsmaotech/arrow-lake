"""Dataset management request/response models."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


def _check_no_traversal(value: str) -> None:
    """Reject path traversal in any form: .., %2e%2e, null bytes, etc."""
    if "\0" in value:
        raise ValueError(f"Null byte not allowed: {value!r}")
    decoded = value.replace("%2e", ".").replace("%2E", ".")
    decoded = decoded.replace("%2f", "/").replace("%2F", "/")
    if ".." in decoded:
        raise ValueError(f"Path traversal not allowed: {value!r}")

# Private IP ranges for SSRF prevention (shared with connectors_http.py).
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::ffff:0.0.0.0/96"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

class IngestFilesRequest(BaseModel):
    """Request body for local file ingestion."""

    file_paths: list[str] = Field(default_factory=list, max_length=100, description="Local file paths to ingest")
    blob_keys: list[str] = Field(default_factory=list, max_length=100, description="MinIO blob keys from prior upload")

    @field_validator("file_paths")
    @classmethod
    def validate_no_traversal(cls, paths: list[str]) -> list[str]:
        for p in paths:
            _check_no_traversal(p)
        return paths

    @field_validator("blob_keys")
    @classmethod
    def validate_blob_keys(cls, keys: list[str]) -> list[str]:
        for k in keys:
            _check_no_traversal(k)
            if not k.startswith("uploads/"):
                raise ValueError(f"Blob key must start with 'uploads/': {k!r}")
        return keys

    @model_validator(mode="after")
    def validate_at_least_one_source(self) -> IngestFilesRequest:
        if not self.file_paths and not self.blob_keys:
            raise ValueError("At least one of file_paths or blob_keys must be provided")
        return self


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
            except ValueError:
                pass  # domain name, not an IP literal
            else:
                if any(addr in net for net in _PRIVATE_NETWORKS):
                    raise ValueError(f"URL hostname resolves to a private IP: {hostname}")
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

    file_paths: list[str] = Field(default_factory=list, max_length=100, description="Image file paths to ingest")
    blob_keys: list[str] = Field(default_factory=list, max_length=100, description="MinIO blob keys from prior upload")

    @field_validator("file_paths")
    @classmethod
    def validate_no_traversal(cls, paths: list[str]) -> list[str]:
        for p in paths:
            _check_no_traversal(p)
        return paths

    @field_validator("blob_keys")
    @classmethod
    def validate_blob_keys(cls, keys: list[str]) -> list[str]:
        for k in keys:
            _check_no_traversal(k)
            if not k.startswith("uploads/"):
                raise ValueError(f"Blob key must start with 'uploads/': {k!r}")
        return keys

    @model_validator(mode="after")
    def validate_at_least_one_source(self) -> IngestImagesRequest:
        if not self.file_paths and not self.blob_keys:
            raise ValueError("At least one of file_paths or blob_keys must be provided")
        return self


class IngestVideosRequest(BaseModel):
    """Request body for video file ingestion."""

    file_paths: list[str] = Field(default_factory=list, max_length=100, description="Video file paths to ingest")
    blob_keys: list[str] = Field(default_factory=list, max_length=100, description="MinIO blob keys from prior upload")

    @field_validator("file_paths")
    @classmethod
    def validate_no_traversal(cls, paths: list[str]) -> list[str]:
        for p in paths:
            _check_no_traversal(p)
        return paths

    @field_validator("blob_keys")
    @classmethod
    def validate_blob_keys(cls, keys: list[str]) -> list[str]:
        for k in keys:
            _check_no_traversal(k)
            if not k.startswith("uploads/"):
                raise ValueError(f"Blob key must start with 'uploads/': {k!r}")
        return keys

    @model_validator(mode="after")
    def validate_at_least_one_source(self) -> IngestVideosRequest:
        if not self.file_paths and not self.blob_keys:
            raise ValueError("At least one of file_paths or blob_keys must be provided")
        return self


class IngestMixedRequest(BaseModel):
    """Request body for mixed-modality ingestion."""

    sources: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Mapping of modality to paths/URLs. Keys: files, urls, images, videos.",
    )
    blob_keys: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Mapping of modality to MinIO blob keys. Keys: files, images, videos, documents.",
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
                _check_no_traversal(p)
                if key != "urls" and p.startswith("/"):
                    raise ValueError(f"Absolute paths not allowed: {p!r}")
        return v

    @field_validator("blob_keys")
    @classmethod
    def validate_blob_keys(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        allowed_keys = {"files", "images", "videos", "documents"}
        for key, keys in v.items():
            if key not in allowed_keys:
                raise ValueError(f"Unknown blob modality key: {key!r}. Allowed: {allowed_keys}")
            for k in keys:
                _check_no_traversal(k)
                if not k.startswith("uploads/"):
                    raise ValueError(f"Blob key must start with 'uploads/': {k!r}")
        return v

    @model_validator(mode="after")
    def validate_at_least_one_source(self) -> IngestMixedRequest:
        has_sources = any(v for v in self.sources.values())
        has_blobs = any(v for v in self.blob_keys.values())
        if not has_sources and not has_blobs:
            raise ValueError("At least one of sources or blob_keys must be provided")
        return self


class IngestDocumentsRequest(BaseModel):
    """Request body for PDF document ingestion."""

    pdf_paths: list[str] = Field(default_factory=list, max_length=100, description="PDF file paths to ingest")
    blob_keys: list[str] = Field(default_factory=list, max_length=100, description="MinIO blob keys from prior upload")

    @field_validator("pdf_paths")
    @classmethod
    def validate_pdf_paths(cls, paths: list[str]) -> list[str]:
        for p in paths:
            _check_no_traversal(p)
            if p.startswith("/"):
                raise ValueError(f"Absolute paths not allowed: {p!r}")
            if not p.lower().endswith(".pdf"):
                raise ValueError(f"Not a PDF file: {p!r}")
        return paths

    @field_validator("blob_keys")
    @classmethod
    def validate_blob_keys(cls, keys: list[str]) -> list[str]:
        for k in keys:
            _check_no_traversal(k)
            if not k.startswith("uploads/"):
                raise ValueError(f"Blob key must start with 'uploads/': {k!r}")
            filename = k.rsplit("/", 1)[-1]
            if not filename.lower().endswith(".pdf"):
                raise ValueError(f"Document blob key must reference a PDF: {k!r}")
        return keys

    @model_validator(mode="after")
    def validate_at_least_one_source(self) -> IngestDocumentsRequest:
        if not self.pdf_paths and not self.blob_keys:
            raise ValueError("At least one of pdf_paths or blob_keys must be provided")
        return self


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


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


class UploadedBlob(BaseModel):
    """Metadata for a single uploaded blob."""

    key: str
    size_bytes: int
    content_type: str = ""


class UploadResponse(BaseModel):
    """Response for file upload to MinIO."""

    success: bool = True
    blobs: list[UploadedBlob] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Presigned upload
# ---------------------------------------------------------------------------


class PresignRequest(BaseModel):
    """Request body for generating presigned upload URLs."""

    filenames: list[str] = Field(..., min_length=1, max_length=20)

    @field_validator("filenames")
    @classmethod
    def validate_filenames(cls, names: list[str]) -> list[str]:
        import re
        _SAFE_RE = re.compile(r"^[a-zA-Z0-9_\-.][a-zA-Z0-9_\-.]*$")
        for n in names:
            _check_no_traversal(n)
            if not n or not _SAFE_RE.match(n):
                raise ValueError(f"Invalid filename: {n!r}")
        return names


class PresignedUpload(BaseModel):
    """A single presigned upload slot."""

    key: str
    upload_url: str


class PresignResponse(BaseModel):
    """Response with presigned upload URLs for direct-to-MinIO upload."""

    success: bool = True
    uploads: list[PresignedUpload] = Field(default_factory=list)


class CleanupResponse(BaseModel):
    """Response for upload blob cleanup."""

    success: bool = True
    deleted_count: int = 0
