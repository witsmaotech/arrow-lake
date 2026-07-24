"""Knowledge graph endpoints: build, query, neighbors, stats, schema, GraphRAG."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_checker, get_lake, require_role
from arrow_lake.api.models.knowledge_graph import (
    GraphRAGQueryRequest,
    KGBuildRequest,
    KGBuildResponse,
    KGBuildStatusResponse,
    KGChatRequest,
    KGChatResponse,
    KGDocType,
    KGRebuildIndexRequest,
    KGExportObsidianRequest,
    KAPruneRequest,
    KARollbackRequest,
    KGDocTypesResponse,
    KGNeighborsResponse,
    KGQueryRequest,
    KGQueryResponse,
    KGSchemaResponse,
    KGGraphResponse,
    KGSearchRequest,
    KGSearchResponse,
    KGStatsResponse,
    KGTemplateDetail,
    KGTemplateDetailResponse,
    KGTemplateSummary,
    KGTemplatesResponse,
)
from arrow_lake.exceptions import KGError
from arrow_lake.knowledge_graph._naming import graph_name_for

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
        task_id = await lake.kg_build(req.dataset, incremental=req.incremental)
        return KGBuildResponse(
            task_id=task_id,
            status="pending",
            message=f"KG build started for dataset '{req.dataset}'",
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
    dataset: str | None = None,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> KGSchemaResponse:
    """Get the graph schema (vertex and edge labels).

    ``dataset`` (lake path) scopes schema to the ``kg_{dataset}`` graph;
    omitted → default graph. Per-dataset ACL is enforced when ``dataset`` is set.
    """
    if dataset is not None and not checker.check_dataset_access(
        role=_user.role, dataset=dataset, action="read"
    ):
        raise HTTPException(status_code=403, detail=f"Read access to dataset '{dataset}' denied")
    try:
        client = lake._get_kg_client()
        if client is None:
            raise HTTPException(status_code=404, detail="Knowledge graph is not enabled")
        g = graph_name_for(dataset) if dataset else None
        schema = await client.get_schema(graph_name=g)
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


@router.get("/graph", response_model=KGGraphResponse)
async def kg_graph(
    dataset: str,
    limit: int = 300,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> KGGraphResponse:
    """Get graph vertices + edges for visualization (capped at ``limit``).

    ``dataset`` (lake path, required) scopes to the ``kg_{dataset}`` graph.
    Edges whose endpoints fall outside the returned vertex set are dropped so
    the visualization never references missing nodes. Per-dataset ACL enforced.
    """
    if not checker.check_dataset_access(role=_user.role, dataset=dataset, action="read"):
        raise HTTPException(status_code=403, detail=f"Read access to dataset '{dataset}' denied")
    try:
        data = await lake.kg_get_graph(dataset, limit=min(max(limit, 1), 1000))
        return KGGraphResponse(**data)
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.post("/search", response_model=KGSearchResponse)
async def kg_search(
    *,
    req: KGSearchRequest,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> KGSearchResponse:
    """[#2] Semantic search over a dataset's Knowledge Abstract (KA).

    Recall entities/relations by meaning (FAISS over node definitions) —
    complementary to Gremlin / neighbor traversal. Requires
    ``extractor_backend=he``. ``dataset`` scopes to the ``kg_{dataset}`` KA
    dump; per-dataset read ACL is enforced.
    """
    _enforce_read_acl(checker, _user, req.dataset)
    try:
        t0 = time.perf_counter()
        result = await lake.kg_search(req.dataset, req.query, top_k=req.top_k)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return KGSearchResponse(
            nodes=result["nodes"],
            edges=result["edges"],
            node_count=result["node_count"],
            edge_count=result["edge_count"],
        )
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.post("/ask", response_model=KGChatResponse)
async def kg_ask(
    *,
    req: KGChatRequest,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> KGChatResponse:
    """[#2] RAG Q&A over a dataset's Knowledge Abstract (KA).

    Retrieves the top-K semantically-relevant nodes/edges and generates an
    answer with the configured LLM. Requires ``extractor_backend=he``.
    ``dataset`` scopes to the ``kg_{dataset}`` KA dump; per-dataset read ACL
    is enforced.
    """
    _enforce_read_acl(checker, _user, req.dataset)
    try:
        result = await lake.kg_chat(
            req.dataset, req.question, top_k=req.top_k, engine=req.engine, history=req.history
        )
        return KGChatResponse(
            answer=result["answer"],
            retrieved_items=result["retrieved_items"],
            retrieval_count=result["retrieval_count"],
            neighbor_context=result.get("neighbor_context", []),
        )
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.post("/ask/stream")
async def kg_ask_stream(
    *,
    req: KGChatRequest,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> StreamingResponse:
    """Stream a GraphRAG KG answer via Server-Sent Events.

    Emits ``metadata`` (retrieved items + neighbor context) once, then
    ``content`` deltas, then ``done`` (or ``error``).
    """
    _enforce_read_acl(checker, _user, req.dataset)

    async def _events() -> AsyncIterator[str]:
        event_id = uuid.uuid4().hex[:8]
        try:
            async with asyncio.timeout(300):
                async for kind, payload in lake.kg_chat_stream(
                    req.dataset, req.question, top_k=req.top_k, history=req.history
                ):
                    if kind == "meta":
                        yield f"id: {event_id}-meta\nevent: metadata\ndata: {json.dumps(payload)}\n\n"
                    else:  # delta token
                        yield f"event: content\ndata: {json.dumps({'data': payload})}\n\n"
                yield f"id: {event_id}-done\nevent: done\ndata: {{}}\n\n"
        except TimeoutError:
            logger.warning("KG stream timed out after 300s")
            yield f"id: {event_id}-error\nevent: error\ndata: {json.dumps({'error': 'Streaming timed out'})}\n\n"
        except KGError as exc:
            yield f"id: {event_id}-error\nevent: error\ndata: {json.dumps({'error': exc.message})}\n\n"
        except Exception:
            logger.exception("KG stream error")
            yield f"id: {event_id}-error\nevent: error\ndata: {json.dumps({'error': 'Internal error during streaming'})}\n\n"

    return StreamingResponse(_events(), media_type="text/event-stream; charset=utf-8")


@router.post("/rebuild-index", dependencies=[Depends(require_role(Role.ADMIN))])
async def kg_rebuild_index(
    *,
    req: KGRebuildIndexRequest,
    lake: Any = Depends(get_lake),
) -> dict[str, Any]:
    """[#7] Rebuild a dataset's KA FAISS index from its dump (no LLM re-extract).

    Lightweight index-only refresh — cheaper than ``kg_build``. Use when the
    index is stale/corrupt or the embedder changed. Requires
    ``extractor_backend=he`` and an existing KA dump. ADMIN-only — mutates the
    KA dump, same privilege level as ``/kg/build``.
    """
    try:
        return await lake.kg_rebuild_index(req.dataset)
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/export-obsidian")
async def kg_export_obsidian(
    *,
    req: KGExportObsidianRequest,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> dict[str, Any]:
    """[#5] Export a dataset's KA as an Obsidian vault (Markdown notes + wikilinks).

    One ``.md`` per node (YAML front-matter), edges as ``[[wikilinks]]``; open
    the folder in Obsidian to roam the graph. Requires ``extractor_backend=he``
    + an existing KA dump. VIEWER+ (read of the dataset's KA); the export dir is
    server-derived (``he_ka_base_dir/{dataset}/obsidian/``) — no caller path.
    """
    _enforce_read_acl(checker, _user, req.dataset)
    try:
        return await lake.kg_export_obsidian(
            req.dataset, vault_name=req.vault_name, overwrite=req.overwrite,
        )
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/ka-versions/{dataset}")
async def kg_list_ka_versions(
    dataset: str,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
    checker=Depends(get_checker),
) -> dict[str, Any]:
    """[#11] List archived KA versions for a dataset (newest first). VIEWER+ read ACL."""
    _enforce_read_acl(checker, _user, dataset)
    try:
        versions = await lake.kg_list_ka_versions(dataset)
        return {"dataset": dataset, "versions": versions, "count": len(versions)}
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.post("/ka-rollback", dependencies=[Depends(require_role(Role.ADMIN))])
async def kg_rollback_ka(
    *,
    req: KARollbackRequest,
    lake: Any = Depends(get_lake),
) -> dict[str, Any]:
    """[#11] Restore a dataset's KA dump to a prior archived version.

    ADMIN-only — mutates the active dump (current is archived first, so rollback
    is reversible). Use ``list-ka-versions`` to find the ``version`` id.
    """
    try:
        return await lake.kg_rollback_ka(req.dataset, req.version)
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/ka-prune", dependencies=[Depends(require_role(Role.ADMIN))])
async def kg_prune_ka_versions(
    *,
    req: KAPruneRequest,
    lake: Any = Depends(get_lake),
) -> dict[str, Any]:
    """[#11] Prune archived KA versions, keeping the newest ``keep``. ADMIN-only."""
    try:
        return await lake.kg_prune_ka_versions(req.dataset, keep=req.keep)
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc


@router.get("/doc-types", response_model=KGDocTypesResponse)
async def kg_doc_types(
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> KGDocTypesResponse:
    """List canonical doc_types with aliases, description, and the template each
    auto-resolves to. Use to pick the right ``doc_type`` for ingest and bypass
    the classifier. Read-only metadata — does not require HugeGraph.
    """
    items = await lake.kg_list_doc_types()
    return KGDocTypesResponse(doc_types=[KGDocType(**i) for i in items])


@router.get("/templates", response_model=KGTemplatesResponse)
async def kg_templates(
    category: str | None = None,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> KGTemplatesResponse:
    """List hyper-extract preset templates, optionally filtered by category.
    ``is_high_risk`` flags hypergraph templates (auto-avoided unless forced)."""
    items = await lake.kg_list_templates(category=category)
    return KGTemplatesResponse(
        templates=[KGTemplateSummary(**i) for i in items], count=len(items)
    )


@router.get("/templates/{template_path:path}", response_model=KGTemplateDetailResponse)
async def kg_template_detail(
    template_path: str,
    lake: Any = Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> KGTemplateDetailResponse:
    """Full detail for one template (e.g. ``general/concept_graph``) — output
    fields, guideline, and risk flag. The ``:path`` converter accepts the slash
    in the template path. 404 if not found."""
    try:
        detail = await lake.kg_describe_template(template_path)
    except KGError as exc:
        raise HTTPException(status_code=_kg_error_to_status(exc), detail=exc.message) from exc
    return KGTemplateDetailResponse(template=KGTemplateDetail(**detail))


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
            dataset_name=req.dataset,
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
