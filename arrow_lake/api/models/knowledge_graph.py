"""Knowledge graph request/response models (M3)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


class KGBuildRequest(BaseModel):
    dataset: str = Field(
        ...,
        min_length=1,
        max_length=256,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Name of the Lance dataset to build KG from",
    )
    incremental: bool = Field(
        default=False,
        description=(
            "Incremental build: feed only NEW chunks (not already in the KA's "
            "fed_chunks) into the existing KA and upsert their entities/edges. "
            "Falls back to a full rebuild when no KA dump exists or the template "
            "changed. Use after appending data; use full rebuild after "
            "re-ingest/delete or a template change."
        ),
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
# Graph visualization (vertices + edges, capped)
# ---------------------------------------------------------------------------


class KGGraphNode(BaseModel):
    id: str
    label: str
    name: str = ""
    type: str = ""
    definition: str = ""


class KGGraphEdge(BaseModel):
    id: str = ""
    source: str
    target: str
    label: str = ""
    relation_type: str = ""


class KGGraphResponse(BaseModel):
    nodes: list[KGGraphNode]
    edges: list[KGGraphEdge]
    vertex_count: int
    edge_count: int
    truncated: bool = False


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
    dataset: str = Field(..., min_length=1, max_length=256, description="Target dataset")
    top_k: int = Field(default=5, ge=1, le=50)
    traversal_depth: int = Field(default=2, ge=1, le=10)
    graph_weight: float = Field(default=0.3, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# [#2] KA Semantic Search / RAG Chat (v1.8.8 — hyper-extract)
# ---------------------------------------------------------------------------


class KGSearchRequest(BaseModel):
    dataset: str = Field(
        ...,
        min_length=1,
        max_length=256,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Lake dataset whose KA to search (the kg_{dataset} graph's KA dump).",
    )
    query: str = Field(..., min_length=1, max_length=2000, description="Natural-language search query")
    top_k: int = Field(default=5, ge=1, le=50, description="Top-K nodes and edges to retrieve")


class KGSearchResponse(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    node_count: int
    edge_count: int


class KGChatRequest(BaseModel):
    dataset: str = Field(
        ...,
        min_length=1,
        max_length=256,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Lake dataset whose KA to query.",
    )
    question: str = Field(..., min_length=1, max_length=5000, description="Natural-language question")
    top_k: int = Field(default=5, ge=1, le=50, description="Top-K nodes and edges fed as RAG context")


class KGChatResponse(BaseModel):
    answer: str
    retrieved_items: list[dict[str, Any]]
    retrieval_count: int


class KGRebuildIndexRequest(BaseModel):
    dataset: str = Field(
        ...,
        min_length=1,
        max_length=256,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Lake dataset whose KA FAISS index to rebuild (must have a KA dump).",
    )


class KGExportObsidianRequest(BaseModel):
    dataset: str = Field(
        ...,
        min_length=1,
        max_length=256,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Lake dataset whose KA to export as an Obsidian vault.",
    )
    vault_name: str = Field(default="Knowledge Vault", max_length=128)
    overwrite: bool = Field(
        default=False,
        description="Overwrite an existing vault at the server-derived export dir.",
    )


class KARollbackRequest(BaseModel):
    dataset: str = Field(..., min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9_-]+$")
    version: str = Field(
        ..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$",
        description="Archived version id (from list-ka-versions). Restricted to path-safe chars "
                    "(defense-in-depth: rollback also requires the id to exist in the manifest).",
    )


class KAPruneRequest(BaseModel):
    dataset: str = Field(..., min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9_-]+$")
    keep: int = Field(default=5, ge=0, le=1000, description="Number of newest versions to keep.")


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
