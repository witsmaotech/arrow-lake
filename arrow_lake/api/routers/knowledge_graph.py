"""Knowledge graph endpoints: build, query, neighbors, stats, schema, GraphRAG."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.models.knowledge_graph import (
    GraphRAGQueryRequest,
    KGBuildRequest,
    KGBuildResponse,
    KGBuildStatusResponse,
    KGNeighborsResponse,
    KGQueryRequest,
    KGQueryResponse,
    KGSchemaResponse,
    KGStatsResponse,
)
from arrow_lake.exceptions import KGError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/kg", tags=["kg"])

# Forbidden Gremlin operations that must never be accepted from user input.
_FORBIDDEN_PATTERNS = [".drop()", ".addV(", ".addE(", ".property(", ".remove(", ".delete("]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_label_names(schema: dict[str, Any], key: str) -> list[str]:
    """Extract label names from a HugeGraph schema response."""
    items = schema.get(key, [])
    if isinstance(items, list):
        return [item.get("name", str(item)) for item in items]
    return []


def _kg_stats_enabled(lake: Any) -> bool:
    """Return whether the KG subsystem is enabled."""
    return getattr(lake._config, "hugegraph", None) is not None and lake._config.hugegraph.enabled


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/build", response_model=KGBuildResponse, dependencies=[Depends(require_role(Role.ADMIN))])
async def kg_build(
    *,
    req: KGBuildRequest,
    lake: Any = Depends(get_lake),
) -> KGBuildResponse:
    """Build a knowledge graph from a dataset."""
    try:
        task_id = await lake.kg_build(req.dataset_name)
        return KGBuildResponse(
            task_id=task_id,
            status="pending",
            message=f"KG build started for dataset '{req.dataset_name}'",
        )
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.get("/build/{task_id}/status", response_model=KGBuildStatusResponse)
async def kg_build_status(
    task_id: str,
    lake: Any = Depends(get_lake),
) -> KGBuildStatusResponse:
    """Get the status of a KG build task."""
    try:
        status = await lake.kg_build_status(task_id)
        if status is None:
            raise HTTPException(status_code=404, detail=f"Build task '{task_id}' not found")
        return KGBuildStatusResponse(
            task_id=status["task_id"],
            status=status["status"],
            dataset_name=status["dataset_name"],
            total_chunks=status.get("total_chunks", 0),
            processed_chunks=status.get("processed_chunks", 0),
            entity_count=status.get("entity_count", 0),
            relation_count=status.get("relation_count", 0),
            error=status.get("error"),
        )
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.get("/schema", response_model=KGSchemaResponse)
async def kg_schema(
    lake: Any = Depends(get_lake),
) -> KGSchemaResponse:
    """Get the graph schema (vertex and edge labels)."""
    try:
        client = lake._get_kg_client()
        if client is None:
            raise HTTPException(status_code=404, detail="Knowledge graph is not enabled")
        schema = await client.get_schema()
        return KGSchemaResponse(
            vertex_labels=_extract_label_names(schema, "vertexlabels"),
            edge_labels=_extract_label_names(schema, "edgelabels"),
        )
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.post("/query", response_model=KGQueryResponse, dependencies=[Depends(require_role(Role.EDITOR))])
async def kg_query(
    *,
    req: KGQueryRequest,
    lake: Any = Depends(get_lake),
) -> KGQueryResponse:
    """Execute a Gremlin query against the knowledge graph."""
    # Block dangerous mutating operations before forwarding to the graph.
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.lower() in req.gremlin.lower():
            raise HTTPException(status_code=400, detail=f"Forbidden Gremlin operation: {pattern}")

    try:
        t0 = time.perf_counter()
        results = await lake.kg_query(req.gremlin)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return KGQueryResponse(results=results, execution_time_ms=elapsed_ms)
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.get("/entities/{entity_id}/neighbors", response_model=KGNeighborsResponse)
async def kg_neighbors(
    entity_id: str,
    depth: int = 1,
    lake: Any = Depends(get_lake),
) -> KGNeighborsResponse:
    """Get neighbor vertices of a given entity."""
    if depth > 5:
        raise HTTPException(status_code=400, detail=f"Depth too large: {depth} (max 5)")
    try:
        neighbors = await lake.kg_get_neighbors(entity_id, depth=depth)
        return KGNeighborsResponse(
            center_id=entity_id,
            neighbors=neighbors,
            depth=depth,
        )
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.get("/stats", response_model=KGStatsResponse)
async def kg_stats(
    lake: Any = Depends(get_lake),
) -> KGStatsResponse:
    """Get knowledge graph statistics."""
    try:
        stats = await lake.kg_stats()
        return KGStatsResponse(
            total_vertices=stats.get("total_vertices", 0),
            total_edges=stats.get("total_edges", 0),
            graph_enabled=True,
        )
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.delete("/graph", dependencies=[Depends(require_role(Role.ADMIN))])
async def kg_delete_graph(
    lake: Any = Depends(get_lake),
) -> dict[str, str]:
    """Delete all data from the knowledge graph."""
    try:
        await lake.kg_delete_graph()
        return {"status": "ok", "message": "Graph data deleted"}
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.post("/query/graphrag", dependencies=[Depends(require_role(Role.VIEWER))])
async def graphrag_query(
    *,
    req: GraphRAGQueryRequest,
    lake: Any = Depends(get_lake),
) -> dict[str, Any]:
    """Run a GraphRAG query (RAG with knowledge graph retrieval)."""
    try:
        rag_resp = await lake.rag_query(
            question=req.question,
            dataset_name=req.dataset_name,
            top_k=req.top_k,
        )
        return {
            "answer": rag_resp.answer,
            "citations": [
                {
                    "chunk_index": c.chunk_index,
                    "dataset": c.dataset,
                    "row_id": c.row_id,
                    "score": c.score,
                    "text_excerpt": c.text_excerpt,
                }
                for c in rag_resp.citations
            ],
            "retrieval_count": rag_resp.retrieval_count,
            "context_tokens": rag_resp.context_tokens,
            "latency_ms": rag_resp.latency_ms,
        }
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _kg_error_to_status(exc: KGError) -> int:
    """Map a KGError to an HTTP status code.

    This mirrors the mapping in arrow_lake.api.errors._error_code_to_http_status
    but is kept local here to avoid an extra import chain.
    """
    from arrow_lake.exceptions import ErrorCode

    code = exc.error_code
    if code == ErrorCode.KG_GRAPH_NOT_FOUND:
        return 404
    if code == ErrorCode.KG_SCHEMA_ERROR:
        return 400
    if code == ErrorCode.KG_TRAVERSAL_TIMEOUT:
        return 504
    if code in (ErrorCode.KG_CONNECTION_FAILED, ErrorCode.KG_EXTRACT_FAILED):
        return 503
    return 500
