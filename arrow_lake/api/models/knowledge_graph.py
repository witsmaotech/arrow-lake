"""Knowledge graph request/response models (M3)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


class KGBuildRequest(BaseModel):
    dataset_name: str = Field(
        ...,
        min_length=1,
        max_length=256,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Name of the Lance dataset to build KG from",
    )


class KGBuildResponse(BaseModel):
    task_id: str
    status: str
    message: str


class KGBuildStatusResponse(BaseModel):
    task_id: str
    status: str
    dataset_name: str
    total_chunks: int = 0
    processed_chunks: int = 0
    entity_count: int = 0
    relation_count: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class KGSchemaResponse(BaseModel):
    vertex_labels: list[str]
    edge_labels: list[str]


# ---------------------------------------------------------------------------
# Gremlin Query
# ---------------------------------------------------------------------------


class KGQueryRequest(BaseModel):
    gremlin: str = Field(..., min_length=1, max_length=10000, description="Gremlin query string")
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    dataset: str | None = Field(
        default=None,
        description="Lake dataset name — scope the query to the kg_{dataset} graph. "
        "When set, a leading `g.` traversal source is rewritten to "
        "`kg_{dataset}.traversal()` so the query reads the per-dataset graph "
        "instead of the default. Omit only when querying the default graph.",
    )


class KGQueryResponse(BaseModel):
    results: list[Any]
    execution_time_ms: float


# ---------------------------------------------------------------------------
# Neighbors
# ---------------------------------------------------------------------------


class KGNeighborsResponse(BaseModel):
    center_id: str
    neighbors: list[dict[str, Any]]
    depth: int


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class KGStatsResponse(BaseModel):
    total_vertices: int
    total_edges: int
    graph_enabled: bool


# ---------------------------------------------------------------------------
# GraphRAG Query
# ---------------------------------------------------------------------------


class GraphRAGQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000, description="User question")
    dataset_name: str = Field(..., min_length=1, max_length=256, description="Target dataset")
    top_k: int = Field(default=5, ge=1, le=50)
    traversal_depth: int = Field(default=2, ge=1, le=10)
    graph_weight: float = Field(default=0.3, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# doc_type / template metadata (v1.8.8)
# ---------------------------------------------------------------------------


class KGDocType(BaseModel):
    doc_type: str
    description: str
    aliases: list[str]
    resolved_template: str = Field(description="Template this doc_type resolves to")
    resolution: str = Field(
        description="Match source: override | gallery | degraded | default"
    )


class KGDocTypesResponse(BaseModel):
    doc_types: list[KGDocType]


class KGTemplateSummary(BaseModel):
    path: str
    category: str
    name: str
    type: str
    tags: list[str]
    is_high_risk: bool
    description_zh: str
    description_en: str


class KGTemplatesResponse(BaseModel):
    templates: list[KGTemplateSummary]
    count: int


class KGTemplateDetail(BaseModel):
    path: str
    category: str
    name: str
    type: str
    tags: list[str]
    is_high_risk: bool
    description_zh: str
    description_en: str
    entity_fields: list[str]
    relation_fields: list[str]
    guideline_zh: str
    guideline_en: str


class KGTemplateDetailResponse(BaseModel):
    template: KGTemplateDetail
