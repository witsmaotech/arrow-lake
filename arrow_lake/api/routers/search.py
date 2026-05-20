"""Search endpoints: vector, FTS, hybrid, faceted, ensemble."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_checker, get_lake, require_role
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
from arrow_lake.api.utils import run_sync

router = APIRouter(prefix="/api/v1/datasets", tags=["search"])

_SEARCH_TIMEOUT = 300


@router.post("/{name}/search/vector", response_model=VectorSearchResponse)
async def vector_search(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: VectorSearchRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> VectorSearchResponse:
    """Vector similarity search on a dataset."""
    result = await run_sync(
        lake.search,
        name,
        req.query_vector,
        top_k=req.top_k,
        metric=req.metric,
        vector_column=req.vector_column,
        where=req.where,
        nprobes=req.nprobes,
        timeout=_SEARCH_TIMEOUT,
        label="vector_search",
    )
    table = checker.apply_table_filter(result.table, dataset=name, role=_user.role)
    resp = arrow_table_to_response(table, req.format, meta={
        "query_vector_dim": result.query_vector_dim,
        "metric": result.metric,
        "top_k": result.top_k,
        "max_distance": result.max_distance,
    })
    return VectorSearchResponse(**resp)


@router.post("/{name}/search/fts", response_model=FullTextSearchResponse)
async def full_text_search(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: FullTextSearchRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> FullTextSearchResponse:
    """Full-text search on a dataset."""
    result = await run_sync(
        lake.text_search,
        name,
        req.query,
        top_k=req.top_k,
        fts_column=req.fts_column,
        where=req.where,
        offset=req.offset,
        timeout=_SEARCH_TIMEOUT,
        label="text_search",
    )
    table = checker.apply_table_filter(result.table, dataset=name, role=_user.role)
    resp = arrow_table_to_response(table, req.format, meta={
        "query": result.query,
        "top_k": result.top_k,
        "fts_column": result.fts_column,
        "max_score": result.max_score,
    })
    return FullTextSearchResponse(**resp)


@router.post("/{name}/search/hybrid", response_model=HybridSearchResponse)
async def hybrid_search(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: HybridSearchRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> HybridSearchResponse:
    """Hybrid vector + full-text search (RRF fusion)."""
    result = await run_sync(
        lake.hybrid_search,
        name,
        req.query_vector,
        req.query_text,
        top_k=req.top_k,
        vector_column=req.vector_column,
        fts_column=req.fts_column,
        where=req.where,
        timeout=_SEARCH_TIMEOUT,
        label="hybrid_search",
    )
    table = checker.apply_table_filter(result.table, dataset=name, role=_user.role)
    resp = arrow_table_to_response(table, req.format, meta={
        "query_text": result.query_text,
        "query_vector_dim": result.query_vector_dim,
        "top_k": result.top_k,
        "rrf_k": result.rrf_k,
        "max_rrf_score": result.max_rrf_score,
    })
    return HybridSearchResponse(**resp)


@router.post("/{name}/search/faceted", response_model=FacetedSearchResponse)
async def faceted_search(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: FacetedSearchRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> FacetedSearchResponse:
    """Vector search with faceted counts."""
    result = await run_sync(
        lake.faceted_search,
        name,
        req.query_vector,
        facets=req.facets,
        top_k=req.top_k,
        vector_column=req.vector_column,
        where=req.where,
        timeout=_SEARCH_TIMEOUT,
        label="faceted_search",
    )
    table = checker.apply_table_filter(result.table, dataset=name, role=_user.role)
    resp = arrow_table_to_response(table, req.format, meta={
        "query_vector_dim": result.query_vector_dim,
        "top_k": result.top_k,
    })
    facets = [
        FacetCountItem(name=f.name, value=f.value, count=f.count)
        for f in result.facets
    ]
    return FacetedSearchResponse(**resp, facets=facets, total_facets=result.total_facets)


@router.post("/{name}/search/ensemble", response_model=EnsembleSearchResponse)
async def ensemble_search(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: EnsembleSearchRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> EnsembleSearchResponse:
    """Ensemble multi-column vector search with RRF fusion."""
    result = await run_sync(
        lake.ensemble_search,
        name,
        req.query_vector,
        columns=req.columns,
        weights=req.weights,
        top_k=req.top_k,
        where=req.where,
        timeout=_SEARCH_TIMEOUT,
        label="ensemble_search",
    )
    table = checker.apply_table_filter(result.table, dataset=name, role=_user.role)
    resp = arrow_table_to_response(table, req.format, meta={
        "columns_searched": list(result.columns_searched),
        "fusion_method": result.fusion_method,
        "top_k": result.top_k,
        "query_vector_dim": result.query_vector_dim,
    })
    return EnsembleSearchResponse(**resp)
