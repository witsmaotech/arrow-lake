"""Embedding computation and index management request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------


class VectorIndexRequest(BaseModel):
    metric: str = ""
    vector_column: str = "text_embedding"
    index_type: str = ""
    num_partitions: int | None = None
    num_sub_vectors: int | None = None
    replace: bool = True


class VectorIndexResponse(BaseModel):
    success: bool = True
    index_info: dict[str, Any]


class FtsIndexRequest(BaseModel):
    fts_column: str | None = None
    replace: bool = True


class FtsIndexResponse(BaseModel):
    success: bool = True
    message: str


# ---------------------------------------------------------------------------
# Standalone embedding computation
# ---------------------------------------------------------------------------


class TextEmbedRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=128)
    model: str = "Qwen/Qwen3-Embedding-0.6B"
    model_source: str = "huggingface"
    normalize: bool = True


class EmbeddingResponse(BaseModel):
    success: bool = True
    embeddings: list[list[float]] = Field(default_factory=list)
    embedding_dim: int = 0
    model: str = ""
    total: int = 0
    null_count: int = 0


class ImageEmbedRequest(BaseModel):
    images: list[str] = Field(..., min_length=1, max_length=32)
    model: str = "openai/clip-vit-base-patch32"
    model_source: str = "modelscope"
