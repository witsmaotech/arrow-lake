"""RAG request/response models (M2)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


_NAME_PATTERN = r"^[a-zA-Z0-9_-]{1,128}$"


class RAGQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=10000, description="The user's question")
    dataset_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=_NAME_PATTERN,
        description="Target Lance dataset",
    )
    top_k: int | None = Field(default=None, ge=1, description="Number of documents to retrieve")
    retrieval_strategy: str | None = Field(
        default=None,
        description="Retrieval strategy: fts, vector, hybrid",
    )
    template_name: str | None = Field(default=None, description="Prompt template name")
    session_id: str | None = Field(default=None, description="Session ID for conversation history")
    use_kg: bool = Field(
        default=True,
        description="Inject knowledge-graph context (GraphRAG). False = pure vector/fts RAG even when hugegraph enabled.",
    )


class RAGCitationResponse(BaseModel):
    chunk_index: int
    dataset: str
    row_id: str
    score: float
    text_excerpt: str


class RAGQueryResponse(BaseModel):
    answer: str
    citations: list[RAGCitationResponse]
    retrieval_count: int
    context_tokens: int | None = None
    latency_ms: float | None = None
    session_id: str | None = None
    verification: dict | None = None  # v1.9.6 P0-1 faithfulness check (support_ratio/sentences)


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------


class RAGExtractRequest(BaseModel):
    dataset_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=_NAME_PATTERN,
        description="Target Lance dataset",
    )
    text_column: str = Field(default="text_content", description="Column containing text to extract from")
    top_k: int | None = Field(default=None, ge=1, description="Number of documents to process")
    template_name: str | None = Field(default=None, description="Prompt template name")


class RAGExtractResponse(BaseModel):
    answer: str
    retrieval_count: int
    context_tokens: int | None = None
    latency_ms: float | None = None


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


class RAGTemplateInfo(BaseModel):
    name: str
    type: str
    description: str


class RAGTemplatesResponse(BaseModel):
    templates: list[RAGTemplateInfo]


# ---------------------------------------------------------------------------
# Session history
# ---------------------------------------------------------------------------


class RAGSessionSummary(BaseModel):
    session_id: str
    turn_count: int
    last_question: str
    last_timestamp: float


class RAGHistoryResponse(BaseModel):
    session_id: str
    turns: list[dict[str, Any]]
