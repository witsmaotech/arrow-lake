"""FastAPI router for Gravitino metadata proxy endpoints."""

from __future__ import annotations

import json
from typing import Any
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, HTTPException, Request

import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/metadata", tags=["metadata"])


def _get_bridge(request: Request) -> Any:
    bridge = getattr(request.app.state, "gravitino_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=503, detail="Gravitino not configured")
    return bridge


def _get_tag_service(request: Request) -> Any:
    svc = getattr(request.app.state, "gravitino_tag_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Gravitino tags not configured")
    return svc


def _get_model_registry(request: Request) -> Any:
    reg = getattr(request.app.state, "gravitino_model_registry", None)
    if reg is None:
        raise HTTPException(status_code=503, detail="Gravitino models not configured")
    return reg


def _gravitino_get(config: Any, path: str) -> dict[str, Any] | None:
    """Helper: GET from Gravitino REST API."""
    url = f"{config.uri}{path}"
    req = UrlRequest(url)
    req.add_header("Accept", "application/vnd.gravitino.v1+json")
    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Catalogs
# ---------------------------------------------------------------------------

@router.get("/catalogs")
def list_catalogs(request: Request) -> dict[str, Any]:
    """List all catalogs in the Gravitino metalake."""
    cfg = request.app.state.config.gravitino
    data = _gravitino_get(cfg, f"/api/metalakes/{cfg.metalake}/catalogs")
    if data is None:
        return {"success": False, "data": [], "error": "Gravitino unreachable", "metadata": {}}
    identifiers = data.get("identifiers", [])
    return {
        "success": True,
        "data": [{"name": i["name"]} for i in identifiers],
        "error": None,
        "metadata": {"total": len(identifiers)},
    }


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

@router.get("/tables")
def list_tables(request: Request) -> dict[str, Any]:
    """List tables in lance-catalog."""
    cfg = request.app.state.config.gravitino
    data = _gravitino_get(
        cfg,
        f"/api/metalakes/{cfg.metalake}/catalogs/lance-catalog/schemas/arrow_lake/tables",
    )
    if data is None:
        return {"success": False, "data": [], "error": "Gravitino unreachable", "metadata": {}}
    identifiers = data.get("identifiers", [])
    return {
        "success": True,
        "data": [{"name": i["name"]} for i in identifiers],
        "error": None,
        "metadata": {"total": len(identifiers)},
    }


@router.get("/tables/{name}")
def get_table(name: str, request: Request) -> dict[str, Any]:
    """Get table details including columns and properties."""
    cfg = request.app.state.config.gravitino
    data = _gravitino_get(
        cfg,
        f"/api/metalakes/{cfg.metalake}/catalogs/lance-catalog/schemas/arrow_lake/tables/{name}",
    )
    if data is None:
        return {"success": False, "data": None, "error": "Table not found", "metadata": {}}
    t = data.get("table", {})
    return {
        "success": True,
        "data": {
            "name": t.get("name"),
            "columns": t.get("columns", []),
            "properties": t.get("properties", {}),
        },
        "error": None,
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

@router.get("/tags")
def list_tags(request: Request) -> dict[str, Any]:
    """List all tags or tags for a specific table."""
    table = request.query_params.get("table")
    tag_svc = _get_tag_service(request)
    try:
        tags = tag_svc.list_tags(table) if table else []
        return {
            "success": True,
            "data": [{"name": t} for t in tags],
            "error": None,
            "metadata": {"total": len(tags)},
        }
    except Exception as exc:
        return {"success": False, "data": [], "error": str(exc), "metadata": {}}


@router.post("/tags")
def create_tag(request: Request) -> dict[str, Any]:
    """Create a new tag."""
    try:
        body = json.loads(request.query_params.get("body", "{}"))
    except Exception:
        body = {}
    name = body.get("name", "")
    comment = body.get("comment", "")
    if not name:
        raise HTTPException(status_code=400, detail="Tag name is required")
    tag_svc = _get_tag_service(request)
    try:
        tag_svc.create_tag(name, comment)
        return {"success": True, "data": {"name": name}, "error": None, "metadata": {}}
    except Exception as exc:
        return {"success": False, "data": None, "error": str(exc), "metadata": {}}


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------

@router.get("/policies")
def list_policies(request: Request) -> dict[str, Any]:
    """List all policies."""
    cfg = request.app.state.config.gravitino
    data = _gravitino_get(cfg, f"/api/metalakes/{cfg.metalake}/policies")
    if data is None:
        return {"success": False, "data": [], "error": "Gravitino unreachable", "metadata": {}}
    identifiers = data.get("identifiers", [])
    return {
        "success": True,
        "data": [{"name": i["name"]} for i in identifiers],
        "error": None,
        "metadata": {"total": len(identifiers)},
    }


@router.post("/policies/retention")
def create_retention_policy(request: Request) -> dict[str, Any]:
    """Create a data retention policy."""
    body = json.loads(request.query_params.get("body", "{}"))
    name = body.get("name", "")
    days = body.get("days", 30)
    if not name:
        raise HTTPException(status_code=400, detail="Policy name is required")
    try:
        from arrow_lake.quality.gravitino_policies import GravitinoPolicyService

        svc = GravitinoPolicyService(request.app.state.config.gravitino)
        svc.create_retention_policy(name, days)
        return {"success": True, "data": {"name": name, "days": days}, "error": None, "metadata": {}}
    except Exception as exc:
        return {"success": False, "data": None, "error": str(exc), "metadata": {}}


@router.post("/policies/masking")
def create_masking_policy(request: Request) -> dict[str, Any]:
    """Create a data masking policy."""
    body = json.loads(request.query_params.get("body", "{}"))
    name = body.get("name", "")
    columns = body.get("columns", [])
    if not name:
        raise HTTPException(status_code=400, detail="Policy name is required")
    try:
        from arrow_lake.quality.gravitino_policies import GravitinoPolicyService

        svc = GravitinoPolicyService(request.app.state.config.gravitino)
        svc.create_masking_policy(name, columns)
        return {"success": True, "data": {"name": name, "columns": columns}, "error": None, "metadata": {}}
    except Exception as exc:
        return {"success": False, "data": None, "error": str(exc), "metadata": {}}


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

@router.post("/statistics/{name}")
def collect_stats(name: str, request: Request) -> dict[str, Any]:
    """Collect and register table statistics."""
    try:
        from arrow_lake.catalog.gravitino_stats import GravitinoStatsCollector

        cfg = request.app.state.config.gravitino
        collector = GravitinoStatsCollector(cfg)
        lake = request.app.state.lake
        stats = collector.collect_table_stats(name, lake._catalog._pool)
        collector.register_stats(name, stats)
        return {"success": True, "data": stats, "error": None, "metadata": {}}
    except Exception as exc:
        return {"success": False, "data": None, "error": str(exc), "metadata": {}}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@router.get("/models")
def list_models(request: Request) -> dict[str, Any]:
    """List all registered models."""
    registry = _get_model_registry(request)
    try:
        models = registry.list_models()
        return {
            "success": True,
            "data": [{"name": m} for m in models],
            "error": None,
            "metadata": {"total": len(models)},
        }
    except Exception as exc:
        return {"success": False, "data": [], "error": str(exc), "metadata": {}}


@router.get("/models/{name}/versions")
def get_model_versions(name: str, request: Request) -> dict[str, Any]:
    """Get version info for a model."""
    registry = _get_model_registry(request)
    try:
        latest = registry.get_latest_version(name)
        production = registry.get_production_version(name)
        versions = []
        if latest:
            versions.append({
                "version": latest.version,
                "uri": latest.uri,
                "aliases": list(latest.aliases),
                "tier": "latest",
            })
        if production and (latest is None or production.version != latest.version):
            versions.append({
                "version": production.version,
                "uri": production.uri,
                "aliases": list(production.aliases),
                "tier": "production",
            })
        return {
            "success": True,
            "data": versions,
            "error": None,
            "metadata": {"model": name, "total": len(versions)},
        }
    except Exception as exc:
        return {"success": False, "data": [], "error": str(exc), "metadata": {}}
