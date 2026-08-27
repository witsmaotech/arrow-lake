"""v1.11.1 MS2 对象层 API — W2.3:entity-map(源系统 ID → 对象 ID)。

显式维护面(ADMIN):批量导入/列表/删除;不挂摄入(热路径红线)。
W4 将在本路由下扩展 Object Set 查询(``POST /query``)与对象类型列表。
system_db 关闭 → 503(沿 ontology/contracts 路由惯例)。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import require_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/objects", tags=["objects"])


def _store(request: Request):
    return getattr(request.app.state, "entity_map_store", None)


def _require_store(store, name: str):
    if store is None:
        raise HTTPException(
            status_code=503, detail=f"system_db disabled; {name} unavailable",
        )
    return store


# ---------------------------------------------------------------------------
# entity-map(显式维护)
# ---------------------------------------------------------------------------


class EntityMapping(BaseModel):
    source_system: str = Field(default="", max_length=200,
                               description="源系统标识,如 SCADA-A / GIS-B")
    source_id: str = Field(..., min_length=1, max_length=500,
                           description="源系统本地 ID")
    object_id: str = Field(..., min_length=1, max_length=500,
                           description="规范对象 ID(契约 identifier 形态)")


class EntityMapBulkRequest(BaseModel):
    scope: str = Field(..., min_length=1, max_length=200,
                       description="dataset(容器)名")
    table: str = Field(..., min_length=1, max_length=200)
    mappings: list[EntityMapping] = Field(..., min_length=1, max_length=10_000)


@router.get("/entity-map", dependencies=[Depends(require_role(Role.ADMIN))])
async def list_entity_map(
    request: Request,
    scope: str = Query(description="dataset (container) name"),
    table: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=10_000),
) -> dict:
    """List entity-map entries for a scope (optionally one table)."""
    store = _require_store(_store(request), "entity map")
    items = store.list_entries(scope=scope, table_name=table, limit=limit)
    return {"success": True, "data": items, "count": len(items)}


@router.post("/entity-map", dependencies=[Depends(require_role(Role.ADMIN))])
async def bulk_upsert_entity_map(req: EntityMapBulkRequest, request: Request) -> dict:
    """Bulk upsert source-id → object-id mappings (idempotent)."""
    store = _require_store(_store(request), "entity map")
    written = store.bulk_upsert([
        {"scope": req.scope, "table_name": req.table, **m.model_dump()}
        for m in req.mappings
    ])
    return {"success": True, "data": {"written": written, "scope": req.scope,
                                      "table": req.table}}


@router.delete("/entity-map", dependencies=[Depends(require_role(Role.ADMIN))])
async def delete_entity_map(
    request: Request,
    scope: str = Query(description="dataset (container) name"),
    table: str = Query(),
    source_system: str = Query(default=""),
    source_id: str = Query(),
) -> dict:
    """Delete one mapping by its four-part key."""
    store = _require_store(_store(request), "entity map")
    deleted = store.delete(
        scope=scope, table_name=table,
        source_system=source_system, source_id=source_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="entity mapping not found")
    return {"success": True, "data": {"deleted": True}}
