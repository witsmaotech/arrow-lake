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


# Document formats the parser (kreuzberg) can parse to text → chunk for KG.
# The /ingest/documents endpoint accepts any of these, mirroring the parser's
# actual capability (PDF + markdown/text/HTML/Office). Keep in sync with
# ``arrow_lake.ingest.document.DocumentParser``.
_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf",
    ".md", ".markdown", ".txt", ".text", ".rst", ".org", ".tex",
    ".docx", ".doc", ".odt", ".rtf",
    ".html", ".htm", ".epub",
    ".pptx", ".ppt", ".xlsx", ".xls",
})

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
    transforms: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional ETL transforms (JSON spec): rename, select, filter, cast, add_constant",
    )

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


class IngestSqlRequest(BaseModel):
    """Request body for SQL database ingestion."""

    sql: str = Field(..., min_length=1, description="SELECT query to execute")
    connection_url: str = Field(..., min_length=1, description="SQLAlchemy connection string")
    partition_col: str | None = Field(default=None, description="Column for parallel partitioned reads")
    num_partitions: int | None = Field(default=None, ge=1, description="Number of read partitions")
    transforms: list[dict[str, Any]] | None = Field(default=None, description="Optional ETL transforms")


class IngestKafkaRequest(BaseModel):
    """Request body for Kafka ingestion."""

    bootstrap_servers: str = Field(..., min_length=1, description="Kafka bootstrap servers")
    topics: list[str] = Field(..., min_length=1, max_length=10, description="Kafka topics to read")
    start: str = Field(default="earliest", description="Start bound: earliest/latest/ISO-8601/offset dict")
    end: str = Field(default="latest", description="End bound")
    json_decode: bool = Field(default=True, description="Auto-decode JSON message values")
    transforms: list[dict[str, Any]] | None = Field(default=None, description="Optional ETL transforms")


class IngestIcebergRequest(BaseModel):
    """Request body for Iceberg table ingestion."""

    table_uri: str = Field(..., min_length=1, description="Iceberg table URI")
    transforms: list[dict[str, Any]] | None = Field(default=None, description="Optional ETL transforms")


class IngestDeltaLakeRequest(BaseModel):
    """Request body for Delta Lake table ingestion."""

    table_uri: str = Field(..., min_length=1, description="Delta Lake table URI")
    version: int | None = Field(default=None, ge=1, description="Optional table version to read")
    transforms: list[dict[str, Any]] | None = Field(default=None, description="Optional ETL transforms")


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
    """Request body for document ingestion (PDF, markdown, text, Office, ...).

    Any format the parser (kreuzberg) can turn into text is accepted; see
    ``_DOCUMENT_EXTENSIONS``. The field is named ``pdf_paths`` for backward
    compatibility but is not limited to PDF.
    """

    pdf_paths: list[str] = Field(default_factory=list, max_length=100, description="Document file paths to ingest (PDF, markdown, text, Office, ...)")
    blob_keys: list[str] = Field(default_factory=list, max_length=100, description="MinIO blob keys from prior upload")
    doc_type: str | None = Field(
        default=None,
        description="Per-ingest document type for KG extraction routing (v1.7.0, e.g. research_paper, report). None = untyped.",
    )

    @field_validator("pdf_paths")
    @classmethod
    def validate_pdf_paths(cls, paths: list[str]) -> list[str]:
        for p in paths:
            _check_no_traversal(p)
            if p.startswith("/"):
                raise ValueError(f"Absolute paths not allowed: {p!r}")
            if not p.lower().endswith(tuple(_DOCUMENT_EXTENSIONS)):
                raise ValueError(
                    f"Unsupported document type: {p!r}. "
                    f"Allowed extensions: {sorted(_DOCUMENT_EXTENSIONS)}"
                )
        return paths

    @field_validator("blob_keys")
    @classmethod
    def validate_blob_keys(cls, keys: list[str]) -> list[str]:
        for k in keys:
            _check_no_traversal(k)
            if not k.startswith("uploads/"):
                raise ValueError(f"Blob key must start with 'uploads/': {k!r}")
            filename = k.rsplit("/", 1)[-1]
            if not filename.lower().endswith(tuple(_DOCUMENT_EXTENSIONS)):
                raise ValueError(
                    f"Document blob key must reference a supported document type: {k!r}. "
                    f"Allowed extensions: {sorted(_DOCUMENT_EXTENSIONS)}"
                )
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
    num_columns: int = 0
    vector_dim: int | None = None
    has_vector_index: bool = False
    has_fts_index: bool = False
    has_kg: bool = False
    size_bytes: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    description: str | None = None


class DatasetDescriptionRequest(BaseModel):
    """Set a human-readable description for a dataset (console)."""

    description: str = Field(default="", max_length=1000)


class DatasetListResponse(BaseModel):
    """Response for listing all datasets."""

    success: bool = True
    datasets: list[DatasetInfo] = Field(default_factory=list)


class SchemaField(BaseModel):
    """A single field in a dataset's authoritative schema."""

    name: str
    type: str
    nullable: bool = True
    comment: str = ""


class SchemaResponse(BaseModel):
    """Authoritative field schema for a dataset (name + Arrow type)."""

    success: bool = True
    name: str
    fields: list[SchemaField] = Field(default_factory=list)
    total: int = 0


class SchemaAnnotateRequest(BaseModel):
    """Set a human-readable comment on one field (writes Arrow field metadata)."""

    field: str
    comment: str = Field(default="", max_length=1000)


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


class SchemaMigrationAction(BaseModel):
    """A single schema migration action."""

    operation: str = Field(description="One of: add_column, alter_column, drop_column")
    column_name: str = Field(description="Column name to add/alter/drop")
    sql_expr: str = Field(default="", description="SQL expression for add_column")
    new_type: str = Field(default="", description="PyArrow type string for alter_column (e.g. 'int32')")


class SchemaMigrationRequest(BaseModel):
    """Request body for schema migration."""

    actions: list[SchemaMigrationAction] = Field(min_length=1, max_length=10)
    dry_run: bool = Field(default=True, description="If true, only validate without applying")


class SchemaMigrationIssue(BaseModel):
    """A single migration issue."""

    action_index: int
    column_name: str
    messages: list[str]


class SchemaMigrationResponse(BaseModel):
    """Response for schema migration."""

    success: bool = True
    dry_run: bool = True
    issues: list[SchemaMigrationIssue] = Field(default_factory=list)
    applied_count: int = 0


# ---------------------------------------------------------------------------
# Row/column ACL
# ---------------------------------------------------------------------------


class SetAclRequest(BaseModel):
    """Request body for setting row/column ACL on a dataset."""

    role: str = Field(..., pattern=r"^(viewer|editor)$", description="Role to apply ACL to")
    visible_columns: list[str] = Field(default_factory=list, description="Column whitelist (empty = all)")
    row_filter: str = Field(default="", description="Simple row filter expression (e.g. 'region == US')")


class AclEntry(BaseModel):
    """A single ACL entry."""

    role: str
    visible_columns: list[str] = []
    row_filter: str = ""


class AclListResponse(BaseModel):
    """Response for listing ACLs on a dataset."""

    success: bool = True
    dataset: str
    acls: list[AclEntry] = Field(default_factory=list)


class AclSetResponse(BaseModel):
    """Response for setting an ACL."""

    success: bool = True
    dataset: str
    role: str


class AclDeleteResponse(BaseModel):
    """Response for deleting an ACL."""

    success: bool = True
    dataset: str
    role: str
    deleted: bool = False
