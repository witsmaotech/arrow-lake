"""Knowledge graph endpoints: build, query, neighbors, stats, schema, GraphRAG."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_checker, get_lake, require_role
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

router = APIRouter(prefix="/api/v1/kg", tags=["kg"])

# Whitelist of allowed Gremlin traversal steps (read-only, no mutation).
_ALLOWED_GREMLIN_STEPS = frozenset({
    "traversal",
    "V", "E", "has", "hasLabel", "hasId", "hasNot",
    "out", "in", "both", "outE", "inE", "bothE", "outV", "inV",
    "values", "valueMap", "elementMap", "properties",
    "count", "limit", "range", "order", "by",
    "select", "as", "where", "path", "dedup",
    "group", "groupCount", "project", "fold",
    "sum", "mean", "max", "min",
    "id", "label", "constant",
    "repeat", "simplePath", "times", "until", "emit", "loops",
    "cyclicPath", "is", "not", "coin", "sample",
})

_GREMLIN_STEP_RE = re.compile(r"\.\s*(\w+)\s*\(")


_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)

_FORBIDDEN_BARE_RE = re.compile(
    r"\.\s*drop\b|\.\s*addV\b|\.\s*addE\b|\.\s*property\b|\.\s*remove\b|\.\s*delete\b",
    re.IGNORECASE,
)


def _validate_gremlin(query: str) -> None:
    """Validate that a Gremlin query only uses whitelisted steps."""
    cleaned = _COMMENT_RE.sub("", query)
    if "{" in cleaned or "}" in cleaned:
        raise HTTPException(status_code=400, detail="Closure syntax not allowed in Gremlin queries")
    if _FORBIDDEN_BARE_RE.search(cleaned):
        raise HTTPException(status_code=400, detail="Mutation steps are forbidden in Gremlin queries")
    for match in _GREMLIN_STEP_RE.finditer(cleaned):
        step = match.group(1)
        if step not in _ALLOWED_GREMLIN_STEPS:
            raise HTTPException(status_code=400, detail=f"Forbidden Gremlin step: {step}")


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
    _user: dict = Depends(require_role(Role.VIEWER)),
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
    _user: dict = Depends(require_role(Role.VIEWER)),
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
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> KGQueryResponse:
    """Execute a Gremlin query against the knowledge graph."""
    # Block dangerous mutating operations before forwarding to the graph.
    _validate_gremlin(req.gremlin)
    # Per-dataset ACL when a dataset is scoped (matches kg_neighbors / kg_stats).
    _enforce_read_acl(checker, _user, req.dataset)

    try:
        t0 = time.perf_counter()
        results = await lake.kg_query(req.gremlin, dataset_name=req.dataset)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return KGQueryResponse(results=results, execution_time_ms=elapsed_ms)
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.get("/entities/{entity_id}/neighbors", response_model=KGNeighborsResponse)
async def kg_neighbors(
    entity_id: str,
    depth: int = 1,
    dataset: str | None = None,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> KGNeighborsResponse:
    """Get neighbor vertices of a given entity.

    ``dataset`` (lake path) scopes the query to the ``kg_{dataset}`` graph;
    omitted → default graph. Per-dataset ACL is enforced when ``dataset`` is set.
    """
    if depth > 5:
        raise HTTPException(status_code=400, detail=f"Depth too large: {depth} (max 5)")
    if dataset is not None and not checker.check_dataset_access(
        role=_user.role, dataset=dataset, action="read"
    ):
        raise HTTPException(status_code=403, detail=f"Read access to dataset '{dataset}' denied")
    try:
        neighbors = await lake.kg_get_neighbors(entity_id, depth=depth, dataset_name=dataset)
        return KGNeighborsResponse(
            center_id=entity_id,
            neighbors=neighbors,
            depth=depth,
        )
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.get("/stats", response_model=KGStatsResponse)
async def kg_stats(
    dataset: str | None = None,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> KGStatsResponse:
    """Get knowledge graph statistics.

    ``dataset`` (lake path) scopes counts to the ``kg_{dataset}`` graph;
    omitted → default graph. Per-dataset ACL is enforced when ``dataset`` is set.
    """
    if dataset is not None and not checker.check_dataset_access(
        role=_user.role, dataset=dataset, action="read"
    ):
        raise HTTPException(status_code=403, detail=f"Read access to dataset '{dataset}' denied")
    try:
        stats = await lake.kg_stats(dataset_name=dataset)
        return KGStatsResponse(
            total_vertices=stats.get("total_vertices", 0),
            total_edges=stats.get("total_edges", 0),
            graph_enabled=True,
        )
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.delete("/graph", dependencies=[Depends(require_role(Role.ADMIN))])
async def kg_delete_graph(
    dataset: str | None = None,
    lake: Any = Depends(get_lake),
) -> dict[str, str]:
    """Delete all data from the knowledge graph.

    ``dataset`` (lake path) clears only the ``kg_{dataset}`` graph;
    omitted → default graph. ADMIN-only (admin bypasses per-dataset ACL).
    """
    try:
        await lake.kg_delete_graph(dataset_name=dataset)
        target = f"dataset '{dataset}' graph" if dataset else "default graph"
        return {"status": "ok", "message": f"{target} cleared"}
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
# Traversers (path-finding) — v1.8.6: per-dataset scoped, ACL-gated
# ---------------------------------------------------------------------------


def _enforce_read_acl(checker, _user, dataset: str | None) -> None:
    """Raise 403 if the user lacks read access on the scoped dataset.

    When ``dataset`` is None the request targets the legacy default graph
    (``hugegraph``), whose access stays role-gated (VIEWER) — the pre-v1.8.6
    behavior preserved for backward compat, consistent with ``kg_stats`` /
    ``kg_neighbors`` / ``kg_query``. Per-dataset ACL applies only to the v1.8.6
    ``kg_{dataset}`` graphs (the new isolation surface).
    """
    if dataset is not None and not checker.check_dataset_access(
        role=_user.role, dataset=dataset, action="read"
    ):
        raise HTTPException(status_code=403, detail=f"Read access to dataset '{dataset}' denied")


class _SimpleTraverseReq(BaseModel):
    source: str
    direction: str = "OUT"
    max_depth: int = 5
    dataset: str | None = None


class _PathTraverseReq(BaseModel):
    source: str
    target: str
    direction: str = "OUT"
    max_depth: int = 10
    dataset: str | None = None


class _WeightedReq(BaseModel):
    source: str
    target: str
    direction: str = "OUT"
    weight_prop: str = "weight"
    max_degree: int = 10000
    dataset: str | None = None


class _SingleSourceReq(BaseModel):
    source: str
    direction: str = "OUT"
    weight_prop: str = "weight"
    max_degree: int = 10000
    dataset: str | None = None


class _MultiNodeReq(BaseModel):
    sources: list[str] = Field(max_length=100)
    targets: list[str] = Field(max_length=100)
    direction: str = "OUT"
    weight_prop: str = "weight"
    max_degree: int = 10000
    dataset: str | None = None


class _TraverseStep(BaseModel):
    """One step of a customized traversal — validated, HugeGraph-compatible.

    Extra fields allowed (HugeGraph accepts ``degree``/``sample``/etc.) but the
    security-relevant ones are typed and bounded.
    """
    model_config = ConfigDict(extra="allow")
    direction: Literal["OUT", "IN", "BOTH"] = "OUT"
    labels: list[str] = Field(default_factory=list, max_length=50)
    max_degree: int | None = Field(default=None, ge=0, le=100000)
    skip_degree: int | None = Field(default=None, ge=0)
    properties: dict[str, Any] = Field(default_factory=dict)


class _CustomizedReq(BaseModel):
    source: str
    steps: list[_TraverseStep] = Field(max_length=20)
    with_vertex: bool = True
    with_edge: bool = True
    dataset: str | None = None


@router.post("/traversers/rays")
async def traverse_rays(
    req: _SimpleTraverseReq,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> dict[str, Any]:
    """Rays — non-cyclic paths from source. ``dataset`` scopes to ``kg_{dataset}``."""
    _enforce_read_acl(checker, _user, req.dataset)
    try:
        return {"results": await lake.kg_rays(
            req.source, direction=req.direction, max_depth=req.max_depth, dataset_name=req.dataset)}
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.post("/traversers/rings")
async def traverse_rings(
    req: _SimpleTraverseReq,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> dict[str, Any]:
    """Rings — cyclic paths from source back to itself."""
    _enforce_read_acl(checker, _user, req.dataset)
    try:
        return {"results": await lake.kg_rings(
            req.source, direction=req.direction, max_depth=req.max_depth, dataset_name=req.dataset)}
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.post("/traversers/crosspoints")
async def traverse_crosspoints(
    req: _PathTraverseReq,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> dict[str, Any]:
    """Crosspoints — vertices on paths between source and target."""
    _enforce_read_acl(checker, _user, req.dataset)
    try:
        return {"results": await lake.kg_crosspoints(
            req.source, req.target, direction=req.direction, max_depth=req.max_depth, dataset_name=req.dataset)}
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.post("/traversers/all-shortest-paths")
async def traverse_all_shortest_paths(
    req: _PathTraverseReq,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> dict[str, Any]:
    """All shortest paths between source and target."""
    _enforce_read_acl(checker, _user, req.dataset)
    try:
        return {"results": await lake.kg_all_shortest_paths(
            req.source, req.target, direction=req.direction, max_depth=req.max_depth, dataset_name=req.dataset)}
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.post("/traversers/weighted-shortest")
async def traverse_weighted_shortest(
    req: _WeightedReq,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> dict[str, Any]:
    """Weighted shortest path between source and target."""
    _enforce_read_acl(checker, _user, req.dataset)
    try:
        return {"result": await lake.kg_weighted_shortest_path(
            req.source, req.target, direction=req.direction, weight_prop=req.weight_prop,
            max_degree=req.max_degree, dataset_name=req.dataset)}
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.post("/traversers/single-source")
async def traverse_single_source(
    req: _SingleSourceReq,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> dict[str, Any]:
    """Single-source shortest path to all reachable vertices."""
    _enforce_read_acl(checker, _user, req.dataset)
    try:
        return {"result": await lake.kg_single_source_shortest_path(
            req.source, direction=req.direction, weight_prop=req.weight_prop,
            max_degree=req.max_degree, dataset_name=req.dataset)}
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.post("/traversers/multi-node")
async def traverse_multi_node(
    req: _MultiNodeReq,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> dict[str, Any]:
    """Multi-node shortest paths between source and target sets."""
    _enforce_read_acl(checker, _user, req.dataset)
    try:
        return {"results": await lake.kg_multi_node_shortest_path(
            req.sources, req.targets, direction=req.direction, weight_prop=req.weight_prop,
            max_degree=req.max_degree, dataset_name=req.dataset)}
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.post("/traversers/customized")
async def traverse_customized(
    req: _CustomizedReq,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> dict[str, Any]:
    """Customized multi-step path traversal."""
    _enforce_read_acl(checker, _user, req.dataset)
    steps = [s.model_dump(exclude_none=True) for s in req.steps]
    try:
        return {"results": await lake.kg_customized_paths(
            req.source, steps, with_vertex=req.with_vertex, with_edge=req.with_edge,
            dataset_name=req.dataset)}
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _kg_error_to_status(exc: KGError) -> int:
    """Map a KGError to an HTTP status code.

    Delegates to the canonical mapping in arrow_lake.api.errors.
    """
    from arrow_lake.api.errors import _error_code_to_http_status

    return _error_code_to_http_status(exc.error_code)
