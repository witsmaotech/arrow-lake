"""Embedding computation and index management request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

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


class ScalarIndexRequest(BaseModel):
    column: str
    index_type: str = "BTREE"
    name: str | None = None
    replace: bool = True


class ScalarIndexResponse(BaseModel):
    success: bool = True
    message: str


class FacetsIndexRequest(BaseModel):
    columns: list[str] | None = None


class FacetsIndexResponse(BaseModel):
    success: bool = True
    results: dict[str, Any] = Field(default_factory=dict)


class IndexInfo(BaseModel):
    """A single index on a dataset (normalized from LanceTable.list_indices)."""

    name: str | None = None
    type: str = ""
    columns: list[str] = Field(default_factory=list)


class ListIndicesResponse(BaseModel):
    success: bool = True
    name: str
    indices: list[IndexInfo] = Field(default_factory=list)


class DropIndexResponse(BaseModel):
    success: bool = True
    name: str
    index_name: str
    message: str = ""


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

    @field_validator("images")
    @classmethod
    def _validate_image_size(cls, images: list[str]) -> list[str]:
        _max_base64_len = 27_000_000  # ~20MB decoded
        for i, img in enumerate(images):
            if len(img) > _max_base64_len:
                raise ValueError(
                    f"Image at index {i} exceeds maximum size (base64 length {len(img)} > {_max_base64_len})"
                )
        return images


class ClipTextEmbedRequest(BaseModel):
    """Request for CLIP/SigLIP text encoding — cross-modal image retrieval (v1.8.0 #6)."""

    texts: list[str] = Field(..., min_length=1, max_length=128)
    model: str = "openai/clip-vit-base-patch32"
    model_source: str = "huggingface"
