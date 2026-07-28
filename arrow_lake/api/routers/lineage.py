"""Data lineage tracking endpoints."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from starlette.responses import PlainTextResponse

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.models.common import _NAME_PATTERN
from arrow_lake.api.models.lineage import (
    LineageEdge,
    LineageGraphResponse,
    LineageGraphStats,
    LineageHistoryResponse,
    LineageImpactItem,
    LineageImpactRequest,
    LineageImpactResponse,
    LineageNode,
    LineageQueryRequest,
    LineageQueryResponse,
    LineageRecordRequest,
    LineageRecordResponse,
    LineageStatsResponse,
)
from arrow_lake.api.utils import run_sync

router = APIRouter(prefix="/api/v1/lineage", tags=["lineage"])

_LINEAGE_TIMEOUT = 60


@router.post("/record", response_model=LineageRecordResponse)
async def lineage_record_event(
    dataset_name: str = Query(..., pattern=_NAME_PATTERN),
    *,
    req: LineageRecordRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> LineageRecordResponse:
    """Record a lineage event for a dataset."""
    # Validate source_datasets against _NAME_PATTERN — they flow into graph
    # rendering (_to_dot/_to_mermaid labels), so unsanitized names are an
    # injection vector (review H4).
    if req.source_datasets:
        bad = [s for s in req.source_datasets if not re.match(_NAME_PATTERN, s)]
        if bad:
            raise HTTPException(status_code=400, detail=f"invalid source_dataset names: {bad}")
    await run_sync(
        lake.lineage_record_event,
        dataset_name, req.operation,
        source_datasets=req.source_datasets,
        transform_type=req.transform_type,
        actor=req.actor, metadata=req.metadata,
        timeout=_LINEAGE_TIMEOUT, label="lineage_record",
    )
    return LineageRecordResponse(
        message=f"Lineage event recorded for dataset '{dataset_name}'"
    )


@router.get("/history/{dataset_name}", response_model=LineageHistoryResponse)
async def lineage_history(
    dataset_name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> LineageHistoryResponse:
    """Get lineage history for a dataset."""
    events = await run_sync(
        lake.lineage_history, dataset_name,
        timeout=_LINEAGE_TIMEOUT, label="lineage_history",
    )
    from dataclasses import asdict
    serialized = [
        e if isinstance(e, dict) else asdict(e) for e in events
    ]
    return LineageHistoryResponse(
        dataset_name=dataset_name,
        events=serialized,
    )


@router.post("/query", response_model=LineageQueryResponse)
async def lineage_query(
    *,
    req: LineageQueryRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> LineageQueryResponse:
    """Query lineage events via SQL.

    走专用 LineageQueryBridge 查 lineage events(VIEWER 可读审计数据,业务数据在 Lance/
    MinIO 不在此连接)。validate_sql_safety 的关键词正则会误杀 operation='create' 等合法
    审计查询,注入防护应靠 bridge 表名白名单/参数化,而非此处粗糙校验。
    """
    result = await run_sync(
        lake.lineage_query, req.sql,
        timeout=_LINEAGE_TIMEOUT, label="lineage_query",
    )
    if hasattr(result, "to_pylist"):
        data = result.to_pylist()
    elif isinstance(result, list):
        data = result
    else:
        data = [result]
    return LineageQueryResponse(data=data)


@router.get("/graph/{dataset_name}", response_model=None)
async def lineage_graph(
    dataset_name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    max_depth: int = Query(default=10, ge=1, le=20),
    max_nodes: int = Query(default=500, ge=1, le=2000),
    format: str = Query(default="json", pattern=r"^(json|mermaid|dot)$"),
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
):
    """Get the full lineage graph for a dataset (upstream + downstream).

    Supports ``json`` (default), ``mermaid``, and ``dot`` output formats.
    ``max_nodes`` caps the graph size (default 500); ``stats.truncated`` is set
    when the cap is hit.
    """
    result = await run_sync(
        lake.lineage_graph, dataset_name,
        max_depth=max_depth,
        max_nodes=max_nodes,
        timeout=_LINEAGE_TIMEOUT, label="lineage_graph",
    )

    if format == "mermaid":
        return _to_mermaid(dataset_name, result)
    if format == "dot":
        return _to_dot(dataset_name, result)

    # Default JSON response
    return LineageGraphResponse(
        dataset_name=dataset_name,
        nodes=[LineageNode(**n) for n in result.get("nodes", [])],
        edges=[
            LineageEdge(**{("from_" if k == "from" else k): v for k, v in e.items()})
            for e in result.get("edges", [])
        ],
        stats=LineageGraphStats(**result.get("stats", {})),
    )


@router.post("/impact", response_model=LineageImpactResponse)
async def lineage_impact(
    *,
    req: LineageImpactRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> LineageImpactResponse:
    """Analyze downstream impact of changing a dataset."""
    result = await run_sync(
        lake.lineage_impact, req.dataset_name,
        timeout=_LINEAGE_TIMEOUT, label="lineage_impact",
    )
    return LineageImpactResponse(
        source_dataset=req.dataset_name,
        impacted_datasets=[LineageImpactItem(**item) for item in result],
    )


@router.get("/stats", response_model=LineageStatsResponse)
async def lineage_stats(
    *,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> LineageStatsResponse:
    """Get lineage tracking statistics."""
    history = await run_sync(
        lake.lineage_history, "__all__",
        timeout=_LINEAGE_TIMEOUT, label="lineage_stats",
    )
    datasets = set()
    total_events = 0
    if isinstance(history, list):
        total_events = len(history)
        for event in history:
            if isinstance(event, dict):
                ds = event.get("dataset_name")
                if ds:
                    datasets.add(ds)
            else:
                ds = getattr(event, "dataset_name", None)
                if ds:
                    datasets.add(ds)
    return LineageStatsResponse(
        total_datasets_tracked=len(datasets),
        total_events=total_events,
    )


# ---------------------------------------------------------------------------
# Visualization helpers — Mermaid / Graphviz DOT
# ---------------------------------------------------------------------------

def _sanitize_node_id(name: str) -> str:
    """Make a dataset name safe for Mermaid/DOT node identifiers."""
    return name.replace(".", "_").replace("-", "_").replace(" ", "_")


def _to_mermaid(dataset_name: str, graph: dict) -> PlainTextResponse:
    """Render lineage graph as Mermaid flowchart syntax."""
    edges = graph.get("edges", [])
    nodes = graph.get("nodes", [])

    lines: list[str] = ["graph LR"]
    seen_nodes: set[str] = set()

    for edge in edges:
        src = _sanitize_node_id(edge.get("from", ""))
        dst = _sanitize_node_id(edge.get("to", ""))
        op = edge.get("operation", "")
        lines.append(f"    {src} -->|{op}| {dst}")
        seen_nodes.add(src)
        seen_nodes.add(dst)

    # Add isolated nodes (no edges)
    for node in nodes:
        nid = _sanitize_node_id(node.get("id", ""))
        if nid and nid not in seen_nodes:
            label = node.get("id", nid)
            lines.append(f"    {nid}[{label}]")

    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="text/x-mermaid",
    )


def _to_dot(dataset_name: str, graph: dict) -> PlainTextResponse:
    """Render lineage graph as Graphviz DOT syntax."""
    edges = graph.get("edges", [])
    nodes = graph.get("nodes", [])

    lines: list[str] = [
        "digraph lineage {",
        "  rankdir=LR;",
        f'  label="Lineage: {dataset_name}";',
    ]

    for node in nodes:
        nid = _sanitize_node_id(node.get("id", ""))
        ntype = node.get("type", "")
        color = {"target": "#4CAF50", "source": "#2196F3", "derived": "#FF9800"}.get(ntype, "#9E9E9E")
        lines.append(f'  {nid} [label="{node.get("id", nid)}", color="{color}"];')

    for edge in edges:
        src = _sanitize_node_id(edge.get("from", ""))
        dst = _sanitize_node_id(edge.get("to", ""))
        op = edge.get("operation", "")
        lines.append(f'  {src} -> {dst} [label="{op}"];')

    lines.append("}")
    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="text/vnd.graphviz",
    )
