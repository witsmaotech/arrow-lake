"""FastAPI router for Gravitino metadata proxy endpoints."""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/metadata", tags=["metadata"])

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_id(value: str, label: str = "identifier") -> None:
    if not _SAFE_ID.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: {value}")


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


def _get_auth_provider(request: Request) -> Any:
    """Get the GravitinoAuthProvider from app state."""
    return getattr(request.app.state, "gravitino_auth_provider", None)


def _gravitino_get(config: Any, path: str, auth_provider: Any = None) -> dict[str, Any] | None:
    """Helper: GET from Gravitino REST API with auth."""
    url = f"{config.uri}{path}"
    req = UrlRequest(url)
    req.add_header("Accept", "application/vnd.gravitino.v1+json")
    if auth_provider is not None:
        auth_provider.authenticate(req)
    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


_LAKE_DS_CACHE: list = [0.0, []]  # [monotonic_ts, names]; mutated in place (no global needed)
_LAKE_DS_TTL_S = 5.0
# Guards the in-place mutation of _LAKE_DS_CACHE across concurrent requests.
_LAKE_DS_CACHE_LOCK = threading.Lock()


def _cached_list_datasets(lake: Any) -> list[str]:
    """lake.list_datasets() with a short TTL (avoids a catalog scan per request)."""
    now = time.monotonic()
    with _LAKE_DS_CACHE_LOCK:
        if _LAKE_DS_CACHE[1] and now - _LAKE_DS_CACHE[0] < _LAKE_DS_TTL_S:
            return _LAKE_DS_CACHE[1]
    # list_datasets() is outside the lock so concurrent reads don't serialize.
    raw = lake.list_datasets()
    out = [(n.name if hasattr(n, "name") else n) for n in raw]
    with _LAKE_DS_CACHE_LOCK:
        _LAKE_DS_CACHE[0] = now
        _LAKE_DS_CACHE[1] = out
    return out


def _lake_table_fallback(lake: Any, name: str) -> dict[str, Any] | None:
    """Build a table-like payload from a lake dataset's schema.

    Used when the Gravitino lance-catalog has no table for ``name`` (the
    dataset exists in the lake but isn't registered in Gravitino) so the
    governance UI still shows real columns.
    """
    try:
        schema = lake.open_dataset(name).schema
    except Exception:
        return None
    columns = [
        {"name": f.name, "type": str(f.type), "nullable": bool(f.nullable)}
        for f in schema
    ]
    return {
        "name": name,
        "columns": columns,
        "properties": {"source": "lake", "location": f"s3://arrow-lake/{name}.lance"},
    }


# ---------------------------------------------------------------------------
# Catalogs
# ---------------------------------------------------------------------------

@router.get("/catalogs")
def list_catalogs(request: Request, _user=Depends(require_role(Role.VIEWER))) -> dict[str, Any]:
    """List all catalogs in the Gravitino metalake."""
    cfg = request.app.state.config.gravitino
    data = _gravitino_get(cfg, f"/api/metalakes/{cfg.metalake}/catalogs", _get_auth_provider(request))
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
def list_tables(request: Request, lake=Depends(get_lake), _user=Depends(require_role(Role.VIEWER))) -> dict[str, Any]:
    """List tables in lance-catalog, falling back to lake datasets when empty."""
    cfg = request.app.state.config.gravitino
    data = _gravitino_get(
        cfg,
        f"/api/metalakes/{cfg.metalake}/catalogs/lance-catalog/schemas/arrow_lake/tables",
        _get_auth_provider(request),
    )
    identifiers = (data or {}).get("identifiers", [])
    if identifiers:
        return {
            "success": True,
            "data": [{"name": i["name"]} for i in identifiers],
            "error": None,
            "metadata": {"total": len(identifiers), "source": "gravitino"},
        }
    # Gravitino lance-catalog empty/unreachable → surface lake datasets so
    # governance isn't bare (datasets are real; Gravitino registration is best-effort).
    try:
        names = _cached_list_datasets(lake)
        return {
            "success": True,
            "data": [{"name": n} for n in names],
            "error": None,
            "metadata": {"total": len(names), "source": "lake-fallback"},
        }
    except Exception as exc:
        return {"success": False, "data": [], "error": str(exc), "metadata": {}}


@router.get("/tables/{name}")
def get_table(name: str, request: Request, lake=Depends(get_lake), _user=Depends(require_role(Role.VIEWER))) -> dict[str, Any]:
    """Get table details; fall back to the lake dataset schema if not in Gravitino."""
    _validate_id(name, "table name")
    cfg = request.app.state.config.gravitino
    data = _gravitino_get(
        cfg,
        f"/api/metalakes/{cfg.metalake}/catalogs/lance-catalog/schemas/arrow_lake/tables/{name}",
        _get_auth_provider(request),
    )
    t = (data or {}).get("table") if data else None
    if t:
        return {
            "success": True,
            "data": {
                "name": t.get("name"),
                "columns": t.get("columns", []),
                "properties": t.get("properties", {}),
            },
            "error": None,
            "metadata": {"source": "gravitino"},
        }
    # Not registered in Gravitino → fall back to the lake dataset's schema.
    fb = _lake_table_fallback(lake, name)
    if fb is None:
        return {"success": False, "data": None, "error": "Table not found", "metadata": {}}
    return {"success": True, "data": fb, "error": None, "metadata": {"source": "lake-fallback"}}


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

@router.get("/tags")
def list_tags(request: Request, _user=Depends(require_role(Role.VIEWER))) -> dict[str, Any]:
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


@router.post("/tags", dependencies=[Depends(require_role(Role.ADMIN))])
def create_tag(request: Request) -> dict[str, Any]:
    """Create a new tag."""
    try:
        body = json.loads(request.query_params.get("body", "{}"))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON in body parameter") from exc
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
def list_policies(request: Request, _user=Depends(require_role(Role.VIEWER))) -> dict[str, Any]:
    """List all policies."""
    cfg = request.app.state.config.gravitino
    data = _gravitino_get(cfg, f"/api/metalakes/{cfg.metalake}/policies", _get_auth_provider(request))
    if data is None:
        return {"success": False, "data": [], "error": "Gravitino unreachable", "metadata": {}}
    identifiers = data.get("identifiers", [])
    return {
        "success": True,
        "data": [{"name": i["name"]} for i in identifiers],
        "error": None,
        "metadata": {"total": len(identifiers)},
    }


@router.post("/policies/retention", dependencies=[Depends(require_role(Role.ADMIN))])
def create_retention_policy(request: Request) -> dict[str, Any]:
    """Create a data retention policy."""
    try:
        body = json.loads(request.query_params.get("body", "{}"))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON in body parameter") from exc
    name = body.get("name", "")
    days = body.get("days", 30)
    if not name:
        raise HTTPException(status_code=400, detail="Policy name is required")
    try:
        from arrow_lake.quality.gravitino_policies import GravitinoPolicyService

        svc = GravitinoPolicyService(request.app.state.config.gravitino)
        svc.create_retention_policy(name, days)
        return {
            "success": True,
            "data": {"name": name, "days": days},
            "error": None,
            "metadata": {},
        }
    except Exception as exc:
        return {"success": False, "data": None, "error": str(exc), "metadata": {}}


@router.post("/policies/masking", dependencies=[Depends(require_role(Role.ADMIN))])
def create_masking_policy(request: Request) -> dict[str, Any]:
    """Create a data masking policy."""
    try:
        body = json.loads(request.query_params.get("body", "{}"))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON in body parameter") from exc
    name = body.get("name", "")
    columns = body.get("columns", [])
    if not name:
        raise HTTPException(status_code=400, detail="Policy name is required")
    try:
        from arrow_lake.quality.gravitino_policies import GravitinoPolicyService

        svc = GravitinoPolicyService(request.app.state.config.gravitino)
        svc.create_masking_policy(name, columns)
        return {
            "success": True,
            "data": {"name": name, "columns": columns},
            "error": None,
            "metadata": {},
        }
    except Exception as exc:
        return {"success": False, "data": None, "error": str(exc), "metadata": {}}


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

@router.post("/statistics/{name}", dependencies=[Depends(require_role(Role.EDITOR))])
def collect_stats(name: str, request: Request) -> dict[str, Any]:
    """Collect and register table statistics."""
    _validate_id(name, "table name")
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
def list_models(request: Request, _user=Depends(require_role(Role.VIEWER))) -> dict[str, Any]:
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
def get_model_versions(name: str, request: Request, _user=Depends(require_role(Role.VIEWER))) -> dict[str, Any]:
    """Get version info for a model."""
    _validate_id(name, "model name")
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


# ---------------------------------------------------------------------------
# Policy Enforcement (v1.4.2)
# ---------------------------------------------------------------------------

@router.post("/policies/enforce", dependencies=[Depends(require_role(Role.ADMIN))])
def enforce_policies(request: Request) -> dict[str, Any]:
    """Manually trigger retention policy enforcement."""
    enforcer = getattr(request.app.state, "retention_enforcer", None)
    if enforcer is None:
        raise HTTPException(status_code=503, detail="Retention enforcer not configured")
    try:
        dry_run = request.query_params.get("dry_run", "false").lower() == "true"
        table = request.query_params.get("table")
        if table:
            _validate_id(table, "table name")
            cleaned = enforcer.enforce_table(table, dry_run=dry_run)
        else:
            cleaned = enforcer.enforce(dry_run=dry_run)
        return {
            "success": True,
            "data": {"tables_cleaned": cleaned, "dry_run": dry_run},
            "error": None,
            "metadata": {},
        }
    except Exception as exc:
        return {"success": False, "data": None, "error": str(exc), "metadata": {}}


# ---------------------------------------------------------------------------
# Lineage (v1.4.2)
# ---------------------------------------------------------------------------

@router.get("/lineage/{name}")
def get_lineage(name: str, request: Request, _user=Depends(require_role(Role.VIEWER))) -> dict[str, Any]:
    """Get lineage information for a table from Gravitino properties."""
    _validate_id(name, "table name")
    cfg = request.app.state.config.gravitino
    data = _gravitino_get(
        cfg,
        f"/api/metalakes/{cfg.metalake}/catalogs/lance-catalog/schemas/arrow_lake/tables/{name}",
        _get_auth_provider(request),
    )
    if data is None:
        return {"success": False, "data": None, "error": "Table not found", "metadata": {}}
    props = data.get("table", {}).get("properties", {})
    try:
        sources = json.loads(props.get("lineage.sources", "[]"))
    except (json.JSONDecodeError, ValueError):
        sources = []
    try:
        outputs = json.loads(props.get("lineage.outputs", "[]"))
    except (json.JSONDecodeError, ValueError):
        outputs = []

    lineage = {
        "table": name,
        "operation": props.get("lineage.operation"),
        "timestamp": props.get("lineage.timestamp"),
        "sources": sources,
        "outputs": outputs,
        "lance_version": props.get("lance.latest_version"),
    }
    return {"success": True, "data": lineage, "error": None, "metadata": {}}
