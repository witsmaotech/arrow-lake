"""Embedding computation and index management endpoints."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Path

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.models.common import _NAME_PATTERN
from arrow_lake.api.models.embedding import (
    EmbeddingResponse,
    FacetsIndexRequest,
    FacetsIndexResponse,
    FtsIndexRequest,
    FtsIndexResponse,
    ClipTextEmbedRequest,
    ImageEmbedRequest,
    ScalarIndexRequest,
    ScalarIndexResponse,
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


@router.post(
    "/{name}/index/scalar",
    response_model=ScalarIndexResponse,
)
async def create_scalar_index(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: ScalarIndexRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> ScalarIndexResponse:
    """Create a scalar index on a column (accelerates metadata filtering)."""
    await run_sync(
        lake.create_scalar_index,
        name,
        column=req.column,
        index_type=req.index_type,
        index_name=req.name,
        replace=req.replace,
        timeout=_INDEX_TIMEOUT,
        label="create_scalar_index",
    )
    return ScalarIndexResponse(
        message=f"Scalar index ({req.index_type}) created on '{req.column}' for dataset '{name}'"
    )


@router.post(
    "/{name}/index/facets",
    response_model=FacetsIndexResponse,
)
async def create_facet_indexes(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: FacetsIndexRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> FacetsIndexResponse:
    """Create scalar indexes on facet columns in bulk."""
    results = await run_sync(
        lake.create_facet_indexes,
        name,
        req.columns,
        timeout=_INDEX_TIMEOUT,
        label="create_facet_indexes",
    )
    return FacetsIndexResponse(results=results or {})


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
            model_name=emb_cfg.model,
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
            model=emb_cfg.model,
            total=len(req.texts),
            null_count=null_count,
        )

    if emb_cfg.backend == EmbeddingBackend.DAFT:
        from arrow_lake.embed.daft_encoder import DaftBatchEncoder

        daft_encoder = DaftBatchEncoder(
            model=emb_cfg.model,
            provider=emb_cfg.daft_provider,
            num_partitions=emb_cfg.daft_num_partitions,
            expected_dim=emb_cfg.expected_dim,
        )
        table = pa.table({"text_content": req.texts})
        vectors, dim = await run_sync(
            daft_encoder.encode_to_vectors,
            table,
            column="text_content",
            timeout=_EMBED_TIMEOUT,
            label="embed_text_daft",
        )
        return EmbeddingResponse(
            embeddings=[v.tolist() for v in vectors],
            embedding_dim=dim,
            model=emb_cfg.model,
            total=len(req.texts),
            null_count=int(sum(1 for v in vectors if not np.any(v))),
        )

    encoder = LocalEmbeddingEncoder(
        model_name=emb_cfg.model,
        model_source="local",
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
            model=emb_cfg.model,
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
        model=emb_cfg.model,
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
        model_name=emb_cfg.model,
        model_source="local",
    )
    result = await run_sync(
        encoder.encode, table,
        timeout=_EMBED_TIMEOUT, label="embed_image",
    )

    embeddings: list[list[float]] = []
    col_name = result.vector_column
    result_table = result.table
    if result_table is not None and col_name in result_table.column_names:
        for val in result_table.column(col_name).to_pylist():
            if val is not None:
                embeddings.append(val)

    return EmbeddingResponse(
        embeddings=embeddings,
        embedding_dim=result.embedding_dim,
        model=emb_cfg.model,
        total=result.total,
        null_count=result.null_count,
    )


@embed_router.post("/clip-text", response_model=EmbeddingResponse)
async def embed_clip_text(
    *,
    req: ClipTextEmbedRequest,
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> EmbeddingResponse:
    """Encode texts via CLIP/SigLIP text tower for cross-modal retrieval (v1.8.0 #6).

    Returns L2-normalized embeddings in the same space as ``/embed/image`` —
    pair with vector search on the ``image_embedding`` column (text → image).
    """
    from arrow_lake.api.deps import get_config
    from arrow_lake.embed.image_encoder import CLIPImageEncoder

    get_config()  # ensure config loaded
    encoder = CLIPImageEncoder(model_name=req.model, model_source=req.model_source)
    vectors = await run_sync(
        encoder.encode_text,
        list(req.texts),
        timeout=_EMBED_TIMEOUT,
        label="embed_clip_text",
    )
    dim = int(vectors.shape[1]) if vectors.size else 0
    return EmbeddingResponse(
        embeddings=[v.tolist() for v in vectors],
        embedding_dim=dim,
        model=req.model,
        total=len(vectors),
        null_count=0,
    )
