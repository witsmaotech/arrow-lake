"""Search endpoints: vector, FTS, hybrid, faceted, ensemble."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from arrow_lake.api.deps import get_lake
from arrow_lake.api.models.common import _NAME_PATTERN, arrow_table_to_response
from arrow_lake.api.models.search import (
    EnsembleSearchRequest,
    EnsembleSearchResponse,
    FacetCountItem,
    FacetedSearchRequest,
    FacetedSearchResponse,
    FullTextSearchRequest,
    FullTextSearchResponse,
    HybridSearchRequest,
    HybridSearchResponse,
    VectorSearchRequest,
    VectorSearchResponse,
)

router = APIRouter(prefix="/api/v1/datasets", tags=["search"])


@router.post("/{name}/search/vector", response_model=VectorSearchResponse)
async def vector_search(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: VectorSearchRequest,
    lake=Depends(get_lake),
) -> VectorSearchResponse:
    """Vector similarity search on a dataset."""
    result = lake.search(
        name,
        req.query_vector,
        top_k=req.top_k,
        metric=req.metric,
        vector_column=req.vector_column,
        where=req.where,
        nprobes=req.nprobes,
    )
    resp = arrow_table_to_response(
        result.table,
        req.format,
        meta={
            "query_vector_dim": result.query_vector_dim,
            "metric": result.metric,
            "top_k": result.top_k,
            "max_distance": result.max_distance,
        },
    )
    return VectorSearchResponse(**resp)


@router.post("/{name}/search/fts", response_model=FullTextSearchResponse)
async def full_text_search(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: FullTextSearchRequest,
    lake=Depends(get_lake),
) -> FullTextSearchResponse:
    """Full-text search on a dataset."""
    result = lake.text_search(
        name,
        req.query,
        top_k=req.top_k,
        fts_column=req.fts_column,
        where=req.where,
    )
    resp = arrow_table_to_response(
        result.table,
        req.format,
        meta={
            "query": result.query,
            "top_k": result.top_k,
            "fts_column": result.fts_column,
            "max_score": result.max_score,
        },
    )
    return FullTextSearchResponse(**resp)


@router.post("/{name}/search/hybrid", response_model=HybridSearchResponse)
async def hybrid_search(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: HybridSearchRequest,
    lake=Depends(get_lake),
) -> HybridSearchResponse:
    """Hybrid vector + full-text search (RRF fusion)."""
    result = lake.hybrid_search(
        name,
        req.query_vector,
        req.query_text,
        top_k=req.top_k,
        vector_column=req.vector_column,
        fts_column=req.fts_column,
        where=req.where,
    )
    resp = arrow_table_to_response(
        result.table,
        req.format,
        meta={
            "query_text": result.query_text,
            "query_vector_dim": result.query_vector_dim,
            "top_k": result.top_k,
            "rrf_k": result.rrf_k,
            "max_rrf_score": result.max_rrf_score,
        },
    )
    return HybridSearchResponse(**resp)


@router.post("/{name}/search/faceted", response_model=FacetedSearchResponse)
async def faceted_search(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: FacetedSearchRequest,
    lake=Depends(get_lake),
) -> FacetedSearchResponse:
    """Vector search with faceted counts."""
    result = lake.faceted_search(
        name,
        req.query_vector,
        facets=req.facets,
        top_k=req.top_k,
        vector_column=req.vector_column,
        where=req.where,
    )
    resp = arrow_table_to_response(
        result.table,
        req.format,
        meta={
            "query_vector_dim": result.query_vector_dim,
            "top_k": result.top_k,
        },
    )
    facets = [
        FacetCountItem(name=f.name, value=f.value, count=f.count)
        for f in result.facets
    ]
    return FacetedSearchResponse(
        **resp,
        facets=facets,
        total_facets=result.total_facets,
    )


@router.post("/{name}/search/ensemble", response_model=EnsembleSearchResponse)
async def ensemble_search(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: EnsembleSearchRequest,
    lake=Depends(get_lake),
) -> EnsembleSearchResponse:
    """Ensemble multi-column vector search with RRF fusion."""
    result = lake.ensemble_search(
        name,
        req.query_vector,
        columns=req.columns,
        weights=req.weights,
        top_k=req.top_k,
        where=req.where,
    )
    resp = arrow_table_to_response(
        result.table,
        req.format,
        meta={
            "columns_searched": list(result.columns_searched),
            "fusion_method": result.fusion_method,
            "top_k": result.top_k,
            "query_vector_dim": result.query_vector_dim,
        },
    )
    return EnsembleSearchResponse(**resp)
