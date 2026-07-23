"""DuckLake materialized view management — global endpoints (v1.9.2 批6-P2).

MV 是全局资源(DuckLakeWorkspace 管理,非 per-dataset),故用独立 router
``/api/v1/materialized`` 而非 datasets 前缀(避免与 datasets.py 的
``GET /{name}`` 路由冲突)。

Requires ``ducklake_enabled=True``; returns 503 otherwise. All routes ADMIN-only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.models.query import MaterializeListResponse, MaterializedView
from arrow_lake.api.utils import run_sync
from arrow_lake.exceptions import ArrowLakeError, QueryError

router = APIRouter(prefix="/api/v1/materialized", tags=["materialized"])

_MV_NAME = r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$"


def _gate(exc: Exception) -> HTTPException:
    """DuckLake 未启用 → 503,其余 QueryError → 400。"""
    msg = str(exc)
    return HTTPException(status_code=503 if "not enabled" in msg else 400, detail=msg)


@router.get("", response_model=MaterializeListResponse)
async def list_materialized(
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> MaterializeListResponse:
    """List materialized DuckLake views with lifecycle metadata."""
    try:
        views = await run_sync(lake.list_materialized, timeout=30, label="list_materialized")
    except (QueryError, ArrowLakeError) as exc:
        raise _gate(exc) from exc
    return MaterializeListResponse(
        views=[MaterializedView(**v) for v in views], count=len(views),
    )


@router.delete("/{view}")
async def drop_materialized(
    view: str = Path(..., pattern=_MV_NAME),
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Drop a single materialized view by name."""
    try:
        dropped = await run_sync(lake.drop_materialized, view, timeout=30, label="drop_materialized")
    except (QueryError, ArrowLakeError) as exc:
        raise _gate(exc) from exc
    if not dropped:
        raise HTTPException(status_code=404, detail=f"Materialized view '{view}' not found")
    return {"success": True, "view_name": view, "dropped": True}


@router.post("/cleanup")
async def cleanup_materialized(
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Drop all expired materialized views (TTL-based)."""
    try:
        dropped = await run_sync(lake.cleanup_materialized, timeout=60, label="cleanup_materialized")
    except (QueryError, ArrowLakeError) as exc:
        raise _gate(exc) from exc
    return {"success": True, "dropped": dropped, "count": len(dropped)}
