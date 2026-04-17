"""Embedding computation and index management endpoints."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Path

from arrow_lake.api.deps import get_lake
from arrow_lake.api.models.common import _NAME_PATTERN
from arrow_lake.api.models.embedding import (
    EmbeddingResponse,
    FtsIndexRequest,
    FtsIndexResponse,
    ImageEmbedRequest,
    TextEmbedRequest,
    VectorIndexRequest,
    VectorIndexResponse,
)

router = APIRouter(prefix="/api/v1/datasets", tags=["embedding"])

embed_router = APIRouter(prefix="/api/v1/embed", tags=["embedding"])


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------


@router.post(
    "/{name}/index/vector",
    response_model=VectorIndexResponse,
)
async def create_vector_index(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: VectorIndexRequest,
    lake=Depends(get_lake),
) -> VectorIndexResponse:
    """Create a vector index on a dataset."""
    info = lake.create_vector_index(
        name,
        metric=req.metric,
        vector_column=req.vector_column,
        index_type=req.index_type,
        num_partitions=req.num_partitions,
        num_sub_vectors=req.num_sub_vectors,
        replace=req.replace,
    )
    return VectorIndexResponse(
        index_info=asdict(info) if hasattr(info, "__dataclass_fields__") else info,
    )


@router.post(
    "/{name}/index/fts",
    response_model=FtsIndexResponse,
)
async def create_fts_index(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: FtsIndexRequest,
    lake=Depends(get_lake),
) -> FtsIndexResponse:
    """Create a full-text search index on a dataset."""
    lake.create_fts_index(name, fts_column=req.fts_column, replace=req.replace)
    return FtsIndexResponse(message=f"FTS index created for dataset '{name}'")


# ---------------------------------------------------------------------------
# Standalone embedding computation
# ---------------------------------------------------------------------------


@embed_router.post("/text", response_model=EmbeddingResponse)
async def embed_text(req: TextEmbedRequest) -> EmbeddingResponse:
    """Compute text embeddings using a local model."""
    import pyarrow as pa

    from arrow_lake.embed.encoder import LocalEmbeddingEncoder

    encoder = LocalEmbeddingEncoder(
        model_name=req.model,
        model_source=req.model_source,
    )
    table = pa.table({"text_content": req.texts})
    result = encoder.encode_column(table, column="text_content")

    # Extract embedding vectors from the result table
    embeddings: list[list[float]] = []
    col_name = result.vector_column
    if col_name in table.column_names:
        for val in table.column(col_name).to_pylist():
            if val is not None:
                embeddings.append(val)

    return EmbeddingResponse(
        embeddings=embeddings,
        embedding_dim=result.embedding_dim,
        model=req.model,
        total=result.total_rows,
        null_count=result.null_rows,
    )


@embed_router.post("/image", response_model=EmbeddingResponse)
async def embed_image(req: ImageEmbedRequest) -> EmbeddingResponse:
    """Compute image embeddings using a CLIP/SigLIP model."""
    import base64

    import pyarrow as pa

    from arrow_lake.embed.image_encoder import CLIPImageEncoder

    # Decode base64 images
    image_bytes: list[bytes] = []
    for img_str in req.images:
        if "," in img_str:
            _, encoded = img_str.split(",", 1)
            image_bytes.append(base64.b64decode(encoded))
        else:
            image_bytes.append(base64.b64decode(img_str))

    table = pa.table({"image": image_bytes})
    encoder = CLIPImageEncoder(
        model_name=req.model,
        model_source=req.model_source,
    )
    result = encoder.encode(table)

    # Extract embedding vectors from the result table
    embeddings: list[list[float]] = []
    col_name = result.vector_column
    if col_name in table.column_names:
        for val in table.column(col_name).to_pylist():
            if val is not None:
                embeddings.append(val)

    return EmbeddingResponse(
        embeddings=embeddings,
        embedding_dim=result.embedding_dim,
        model=req.model,
        total=result.total,
        null_count=result.null_count,
    )
