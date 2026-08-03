"""v1.10.0 M5: admin endpoints for the dynamic doc_type ↔ category dictionary.

The doc_type taxonomy was a code-level constant; M5 promotes it to a runtime
dictionary so an admin can add a category (e.g. ``security``) that is
immediately usable as a template ``category`` (Layer-2 routing ==
``template.category == doc_type``) and as an ingest ``doc_type``.

Read access for UI dropdowns is the existing ``GET /api/v1/kg/doc-types`` (VIEWER,
dynamic via the facade). This router is the admin CRUD surface
(``/api/v1/admin/doc-type-categories``): list / add / delete. Mutations refresh
the gallery cache so newly added categories are usable without restart.
"""

from __future__ import annotations

import logging

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import require_role
from arrow_lake.system_db.stores.doc_type_categories import CategoryExistsError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/doc-type-categories", tags=["admin"])

_NAME_RE = r"^[a-z][a-z0-9_]{0,63}$"


def _store(request: Request):
    """The doc_type-category store (None when system_db is disabled — degrade)."""
    return getattr(request.app.state, "doc_type_category_store", None)


class CategoryCreate(BaseModel):
    name: str = Field(..., pattern=_NAME_RE,
                      description="lowercase identifier, e.g. security / aerospace")
    desc_zh: str | None = Field(default=None, max_length=200)
    desc_en: str | None = Field(default=None, max_length=200)
    # Each alias: ≤40 chars, no commas (the store serializes aliases as a
    # comma-joined TEXT column, so a comma would corrupt the round-trip).
    aliases: list[Annotated[str, StringConstraints(max_length=40, pattern=r"^[^,]+$")]] | None = Field(
        default=None, max_length=50)


@router.get("")
async def list_categories(
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """List all doc_type categories (seed + custom)."""
    store = _store(request)
    if store is None:
        # system_db disabled — return the static taxonomy so the UI still works.
        from arrow_lake.knowledge_graph.doc_type_router import (
            DOC_TYPE_ALIASES, DOC_TYPE_DESCRIPTIONS,
        )
        items = [{
            "name": dt, "desc_en": DOC_TYPE_DESCRIPTIONS[dt], "desc_zh": "",
            "aliases": list(DOC_TYPE_ALIASES.get(dt, ())), "source": "seed",
        } for dt in DOC_TYPE_DESCRIPTIONS]
        return {"success": True, "data": items, "count": len(items)}
    items = store.list_categories()
    return {"success": True, "data": items, "count": len(items)}


@router.post("", status_code=201)
async def add_category(
    req: CategoryCreate,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Add a custom doc_type category (immediately usable for templates + ingest)."""
    store = _store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; category management unavailable")
    try:
        store.add_category(
            req.name, desc_zh=req.desc_zh, desc_en=req.desc_en, aliases=req.aliases)
    except CategoryExistsError as exc:  # duplicate → 409 conflict
        raise HTTPException(status_code=409, detail={
            "code": "CATEGORY_DUPLICATE", "message": str(exc)}) from exc
    except ValueError as exc:  # invalid name pattern → 422
        raise HTTPException(status_code=422, detail={
            "code": "CATEGORY_INVALID", "message": str(exc)}) from exc
    # NOTE: no reset_gallery_cache() — the gallery indexes template YAML files,
    # not the category dictionary, so a dictionary change rebuilds a byte-
    # identical gallery (pure waste) and only clears the local worker's cache.
    logger.info("doc_type_category_added name=%s by=%s",
                req.name, getattr(_user, "username", None))
    return {"success": True, "data": {"name": req.name, "source": "custom"}}


@router.delete("/{name}")
async def delete_category(
    name: str,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Delete a doc_type category (seed or custom)."""
    store = _store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; category management unavailable")
    removed = store.delete_category(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"category not found: {name}")
    # no reset_gallery_cache() — see add_category: gallery does not read this table.
    logger.info("doc_type_category_deleted name=%s by=%s",
                name, getattr(_user, "username", None))
    return {"success": True, "data": {"name": name, "deleted": True}}
