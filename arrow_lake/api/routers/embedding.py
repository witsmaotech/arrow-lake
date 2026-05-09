"""Embedding computation and index management endpoints."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Path

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
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
from arrow_lake.api.utils import run_sync

router = APIRouter(prefix="/api/v1/datasets", tags=["embedding"])

embed_router = APIRouter(prefix="/api/v1/embed", tags=["embedding"])

_INDEX_TIMEOUT = 600
_EMBED_TIMEOUT = 120


@router.post(
    "/{name}/index/vector",
    response_model=VectorIndexResponse,
)
async def create_vector_index(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: VectorIndexRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> VectorIndexResponse:
    """Create a vector index on a dataset."""
    info = await run_sync(
        lake.create_vector_index,
        name,
        metric=req.metric,
        vector_column=req.vector_column,
        index_type=req.index_type,
        num_partitions=req.num_partitions,
        num_sub_vectors=req.num_sub_vectors,
        replace=req.replace,
        timeout=_INDEX_TIMEOUT,
        label="create_vector_index",
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
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> FtsIndexResponse:
    """Create a full-text search index on a dataset."""
    await run_sync(
        lake.create_fts_index, name,
        fts_column=req.fts_column, replace=req.replace,
        timeout=_INDEX_TIMEOUT,
        label="create_fts_index",
    )
    return FtsIndexResponse(message=f"FTS index created for dataset '{name}'")


@embed_router.post("/text", response_model=EmbeddingResponse)
async def embed_text(
    req: TextEmbedRequest,
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> EmbeddingResponse:
    """Compute text embeddings using local model or external API."""
    import numpy as np
    import pyarrow as pa

    from arrow_lake.api.deps import get_config
    from arrow_lake.config.media import EmbeddingBackend
    from arrow_lake.embed.encoder import ApiEmbeddingEncoder, LocalEmbeddingEncoder

    cfg = get_config()
    emb_cfg = cfg.embedding

    if emb_cfg.backend == EmbeddingBackend.OPENAI and emb_cfg.api_base:
        api_encoder = ApiEmbeddingEncoder(
            api_base=emb_cfg.api_base,
            api_key=emb_cfg.api_key,
            model_name=req.model,
            batch_size=emb_cfg.batch_size,
        )
        batch = await run_sync(
            api_encoder.encode, req.texts,
            timeout=_EMBED_TIMEOUT, label="embed_text_api",
        )
        embeddings = [e.tolist() for e in batch.embeddings]
        embedding_dim = len(embeddings[0]) if embeddings else 0
        null_count = sum(1 for m in batch.null_mask if m)

        return EmbeddingResponse(
            embeddings=embeddings,
            embedding_dim=embedding_dim,
            model=req.model,
            total=len(req.texts),
            null_count=null_count,
        )

    encoder = LocalEmbeddingEncoder(
        model_name=req.model,
        model_source=req.model_source,
    )
    table = pa.table({"text_content": req.texts})
    result = await run_sync(
        encoder.encode_column, table, column="text_content",
        timeout=_EMBED_TIMEOUT, label="embed_text",
    )

    if result.embedded_rows == 0 or result.embedding_dim == 0:
        return EmbeddingResponse(
            embeddings=[],
            embedding_dim=0,
            model=req.model,
            total=result.total_rows,
            null_count=result.null_rows,
        )

    model = await run_sync(encoder._load_model, timeout=_EMBED_TIMEOUT, label="load_model")
    embeddings_list = await run_sync(
        model.encode, req.texts, normalize_embeddings=True,
        timeout=_EMBED_TIMEOUT, label="model_encode",
    )
    embeddings = [e.tolist() for e in np.asarray(embeddings_list, dtype=np.float32)]

    return EmbeddingResponse(
        embeddings=embeddings,
        embedding_dim=result.embedding_dim,
        model=req.model,
        total=result.total_rows,
        null_count=result.null_rows,
    )


@embed_router.post("/image", response_model=EmbeddingResponse)
async def embed_image(
    req: ImageEmbedRequest,
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> EmbeddingResponse:
    """Compute image embeddings using a CLIP/SigLIP model."""
    import base64

    import pyarrow as pa
    from fastapi import HTTPException

    from arrow_lake.api.deps import get_config
    from arrow_lake.config.media import EmbeddingBackend
    from arrow_lake.embed.image_encoder import CLIPImageEncoder

    cfg = get_config()
    emb_cfg = cfg.embedding

    if emb_cfg.backend == EmbeddingBackend.OPENAI and emb_cfg.api_base:
        raise HTTPException(
            status_code=501,
            detail="Image embedding via external API is not yet supported. "
            "Set ARROW_LAKE__EMBEDDING__BACKEND=local for image embeddings.",
        )

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
    result = await run_sync(
        encoder.encode, table,
        timeout=_EMBED_TIMEOUT, label="embed_image",
    )

    embeddings: list[list[float]] = []
    col_name = result.vector_column
    if col_name in result.column_names:
        for val in result.column(col_name).to_pylist():
            if val is not None:
                embeddings.append(val)

    return EmbeddingResponse(
        embeddings=embeddings,
        embedding_dim=result.embedding_dim,
        model=req.model,
        total=result.total,
        null_count=result.null_count,
    )
