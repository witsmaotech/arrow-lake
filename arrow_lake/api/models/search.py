"""Search request/response models (Sprint 3)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

class FormatMixin(BaseModel):
    """Mixin adding a format selector to search requests."""

    format: Literal["arrow_ipc", "json"] = Field(
        default="json",
        description="Response format: 'arrow_ipc' (base64 IPC stream) or 'json' (rows list)",
    )


# ---------------------------------------------------------------------------
# Vector search
# ---------------------------------------------------------------------------

class VectorSearchRequest(FormatMixin):
    query_vector: list[float] = Field(..., min_length=1)
    top_k: int = Field(default=10, ge=1)
    metric: str | None = None
    vector_column: str = "text_embedding"
    where: str | None = Field(default=None, max_length=4096)
    nprobes: int | None = None


class VectorSearchResponse(BaseModel):
    success: bool = True
    format: str
    row_count: int
    column_count: int
    meta: dict[str, Any] | None = None
    data: str | None = None  # base64 IPC when format=arrow_ipc
    rows: list[dict[str, Any]] | None = None  # when format=json


# ---------------------------------------------------------------------------
# Full-text search
# ---------------------------------------------------------------------------

class FullTextSearchRequest(FormatMixin):
    query: str = Field(..., min_length=1)
    top_k: int | None = None
    fts_column: str | None = None
    where: str | None = Field(default=None, max_length=4096)


class FullTextSearchResponse(BaseModel):
    success: bool = True
    format: str
    row_count: int
    column_count: int
    meta: dict[str, Any] | None = None
    data: str | None = None
    rows: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Hybrid search
# ---------------------------------------------------------------------------

class HybridSearchRequest(FormatMixin):
    query_vector: list[float] = Field(..., min_length=1)
    query_text: str = Field(..., min_length=1)
    top_k: int | None = None
    vector_column: str = "text_embedding"
    fts_column: str | None = None
    where: str | None = Field(default=None, max_length=4096)


class HybridSearchResponse(BaseModel):
    success: bool = True
    format: str
    row_count: int
    column_count: int
    meta: dict[str, Any] | None = None
    data: str | None = None
    rows: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Faceted search
# ---------------------------------------------------------------------------

class FacetCountItem(BaseModel):
    name: str
    value: str
    count: int


class FacetedSearchRequest(FormatMixin):
    query_vector: list[float] = Field(..., min_length=1)
    facets: list[str] | None = None
    top_k: int = Field(default=10, ge=1)
    vector_column: str = "embedding"
    where: str | None = Field(default=None, max_length=4096)


class FacetedSearchResponse(BaseModel):
    success: bool = True
    format: str
    row_count: int
    column_count: int
    meta: dict[str, Any] | None = None
    data: str | None = None
    rows: list[dict[str, Any]] | None = None
    facets: list[FacetCountItem] | None = None
    total_facets: int | None = None


# ---------------------------------------------------------------------------
# Ensemble search
# ---------------------------------------------------------------------------

class EnsembleSearchRequest(FormatMixin):
    query_vector: list[float] = Field(..., min_length=1)
    columns: list[str] | None = None
    weights: dict[str, float] | None = None
    top_k: int | None = None
    where: str | None = Field(default=None, max_length=4096)


class EnsembleSearchResponse(BaseModel):
    success: bool = True
    format: str
    row_count: int
    column_count: int
    meta: dict[str, Any] | None = None
    data: str | None = None
    rows: list[dict[str, Any]] | None = None
