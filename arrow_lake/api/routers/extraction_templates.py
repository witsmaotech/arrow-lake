"""v1.10.0 P6: admin endpoints for user extraction-template management.

CRUD over the writable user-templates volume (``/data/lake/templates/``).
Mutations validate the YAML (:mod:`template_registry`), write to disk, and
:func:`reset_gallery_cache` so the next ``kg_build`` picks up the change with no
rebuild/restart. System/project templates are read-only.

The libSQL metadata store (P3) is wired in M2 for the per-dataset binding UI;
M1 ships CRUD with the YAML as single source of truth (gallery lists everything).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import require_role, get_lake
from arrow_lake.knowledge_graph.doc_type_router import (
    get_template_gallery, reset_gallery_cache,
)
from arrow_lake.knowledge_graph.template_registry import (
    TemplateValidationError,
    content_hash, delete_template, save_template, validate_template_yaml,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/extraction-templates", tags=["admin"])

_USER_DIR_ENV = "ARROW_LAKE__HUGEGRAPH__HE_USER_TEMPLATES_DIR"
_USER_DIR_DEFAULT = "/data/lake/templates"


# --- helpers ---------------------------------------------------------------

def _user_dir() -> str:
    import os
    return os.environ.get(_USER_DIR_ENV, _USER_DIR_DEFAULT)


def _store(request: Request):
    """The extraction-template store (None when system_db is disabled — degrade)."""
    return getattr(request.app.state, "extraction_template_store", None)


def _reserved_names() -> set[str]:
    """System + project template names (user templates must not shadow them)."""
    return {t.name for t in get_template_gallery().templates if t.source == "system"}


def _find(name: str) -> Any | None:
    for t in get_template_gallery().templates:
        if t.name == name.lower():
            return t
    return None


def _inject_doc_type_tag(yaml_text: str, doc_type: str | None) -> str:
    """Add ``doc_type`` to the YAML's ``tags`` so gallery routing (Layer 2 tag
    match) finds it — fixes the v1.10.0 小坑 where the API ``doc_type`` field
    only reached system_db, not the routing gallery. Surgical regex (preserves
    user formatting); ``tags`` is a valid hyper-extract field (safe to touch).
    """
    import re
    if not doc_type:
        return yaml_text
    dt = doc_type.strip().lower()
    if not dt:
        return yaml_text
    # flow list:  tags: [a, b]
    m = re.search(r'^(\s*tags:\s*\[)([^\]]*)\]\s*$', yaml_text, re.M)
    if m:
        items = [x.strip().strip('"\'') for x in m.group(2).split(',') if x.strip()]
        if dt not in items:
            items.append(dt)
        return yaml_text[:m.start()] + f"{m.group(1)}{', '.join(items)}]" + yaml_text[m.end():]
    # block list:  tags:\n  - a\n  - b
    m = re.search(r'^(\s*tags:\s*\n)((?:[ \t]+-.*\n?)+)', yaml_text, re.M)
    if m:
        lines = [l for l in m.group(2).splitlines() if l.strip()]
        items = [re.search(r'-\s*(.+)', l).group(1).strip().strip('"\'') for l in lines]
        if dt not in items:
            indent = (re.match(r'([ \t]*)', lines[0]).group(1) if lines else "  ")
            block = m.group(2).rstrip("\n") + f"\n{indent}- {dt}\n"
        else:
            block = m.group(2)
        return yaml_text[:m.start()] + m.group(1) + block + yaml_text[m.end():]
    # no tags block → add `tags: [dt]` right after the `name:` line
    m = re.search(r'^(\s*name:\s*.*)$', yaml_text, re.M)
    if m:
        return yaml_text[:m.end()] + f"\ntags: [{dt}]" + yaml_text[m.end():]
    return yaml_text + f"\ntags: [{dt}]\n"


# --- models ----------------------------------------------------------------

class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    yaml: str = Field(..., min_length=1)
    doc_type: str | None = None
    description: str | None = None


class TemplateUpdate(BaseModel):
    yaml: str = Field(..., min_length=1)
    doc_type: str | None = None
    description: str | None = None


class TemplateValidateRequest(BaseModel):
    yaml: str = Field(..., min_length=1)


# --- endpoints -------------------------------------------------------------

@router.get("")
async def list_templates(
    source: str | None = None,
    selectable: bool = False,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """List extraction templates (system + project read-only, user editable)."""
    templates = get_template_gallery().templates
    if source:
        templates = [t for t in templates if t.source == source]
    items = []
    for t in templates:
        s = t.to_summary()
        if selectable:
            s["selectable"] = True  # all indexed templates are bindable
        items.append(s)
    return {"success": True, "data": items, "count": len(items)}


@router.get("/{name}")
async def get_template(
    name: str,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Detail: parsed metadata + raw YAML text (user/project file OR preset)."""
    t = _find(name)
    if t is None:
        raise HTTPException(status_code=404, detail=f"template not found: {name}")
    detail = t.to_detail()
    detail["yaml"] = _read_yaml(t.path)
    return {"success": True, "data": detail}


def _read_yaml(path: str) -> str | None:
    """Read raw YAML for a template path (file path OR preset ``category/name``)."""
    # user/project template: absolute .yaml file path
    if path.endswith(".yaml"):
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None
    # preset: category/name → <hyperextract>/templates/presets/<category>/<name>.yaml
    if "/" in path:
        try:
            import hyperextract
            import os
            cat, _, nm = path.partition("/")
            preset_file = os.path.join(
                os.path.dirname(hyperextract.__file__), "templates", "presets", cat, f"{nm}.yaml")
            if os.path.isfile(preset_file):
                with open(preset_file, encoding="utf-8") as fh:
                    return fh.read()
        except Exception:  # noqa: BLE001 — hyperextract missing / unreadable preset
            return None
    return None


@router.post("/validate")
async def validate(
    req: TemplateValidateRequest,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Validate a YAML without saving. Returns field-level errors."""
    try:
        validate_template_yaml(req.yaml, reserved_names=_reserved_names())
        return {"success": True, "data": {"valid": True}}
    except TemplateValidationError as exc:
        return {"success": True, "data": {
            "valid": False, "errors": [{"path": p, "message": m} for p, m in exc.errors]}}


@router.post("", status_code=201)
async def create_template(
    req: TemplateCreate,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Create a user template: validate → write → refresh gallery cache."""
    if req.name in _reserved_names():
        raise HTTPException(status_code=409, detail=f"template name conflicts with system template: {req.name}")
    try:
        yaml = _inject_doc_type_tag(req.yaml, req.doc_type)
        path = save_template(req.name, yaml, _user_dir(), reserved_names=_reserved_names())
    except TemplateValidationError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "TEMPLATE_INVALID",
            "errors": [{"path": p, "message": m} for p, m in exc.errors]}) from exc
    reset_gallery_cache()
    logger.info("extraction_template_created name=%s by=%s hash=%s",
                req.name, getattr(_user, "username", None), content_hash(req.yaml)[:12])
    return {"success": True, "data": {"name": req.name, "path": str(path), "source": "user"}}


@router.put("/{name}")
async def update_template(
    name: str,
    req: TemplateUpdate,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Update a user template (system/project → 403)."""
    existing = _find(name)
    if existing is not None and existing.source != "user":
        raise HTTPException(status_code=403, detail={"code": "TEMPLATE_READ_ONLY",
                                                      "message": f"{name} is a read-only {existing.source} template"})
    try:
        yaml = _inject_doc_type_tag(req.yaml, req.doc_type)
        path = save_template(name, yaml, _user_dir(), reserved_names=_reserved_names())
    except TemplateValidationError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "TEMPLATE_INVALID",
            "errors": [{"path": p, "message": m} for p, m in exc.errors]}) from exc
    reset_gallery_cache()
    logger.info("extraction_template_updated name=%s by=%s hash=%s",
                name, getattr(_user, "username", None), content_hash(req.yaml)[:12])
    return {"success": True, "data": {"name": name, "path": str(path)}}


@router.delete("/{name}")
async def remove_template(
    name: str,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Delete a user template (system/project → 403; in-use → 422)."""
    existing = _find(name)
    if existing is not None and existing.source != "user":
        raise HTTPException(status_code=403, detail={"code": "TEMPLATE_READ_ONLY",
                                                      "message": f"{name} is a read-only {existing.source} template"})
    store = _store(request)
    if store is not None:
        bound = store.list_bindings(name)
        if bound:
            raise HTTPException(status_code=422, detail={
                "code": "TEMPLATE_IN_USE",
                "message": f"template {name} is bound to dataset(s): {bound}. Unbind first.",
            })
    removed = delete_template(name, _user_dir())
    if not removed:
        raise HTTPException(status_code=404, detail=f"template not found: {name}")
    reset_gallery_cache()
    logger.info("extraction_template_deleted name=%s by=%s", name, getattr(_user, "username", None))
    return {"success": True, "data": {"name": name, "deleted": True}}


# --- per-dataset bindings (persisted in system_db) ------------------------

class BindingRequest(BaseModel):
    template: str = Field(..., min_length=1, description="template name to bind")


@router.get("/bindings/{dataset}")
async def get_binding(
    dataset: str,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Which template is bound to ``dataset`` (None if unbound)."""
    store = _store(request)
    bound = store.get_binding(dataset) if store is not None else None
    return {"success": True, "data": {"dataset": dataset, "template": bound}}


@router.put("/bindings/{dataset}")
async def set_binding(
    dataset: str,
    req: BindingRequest,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Bind ``dataset`` to a template (used automatically by /kg/build)."""
    if _find(req.template) is None:
        raise HTTPException(status_code=404, detail=f"template not found: {req.template}")
    store = _store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; binding unavailable")
    store.set_binding(dataset, req.template, bound_by=getattr(_user, "username", None))
    logger.info("template_bound dataset=%s template=%s by=%s",
                dataset, req.template, getattr(_user, "username", None))
    return {"success": True, "data": {"dataset": dataset, "template": req.template}}


@router.delete("/bindings/{dataset}")
async def clear_binding(
    dataset: str,
    request: Request,
    _user: dict = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Remove a dataset's template binding."""
    store = _store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; binding unavailable")
    cleared = store.clear_binding(dataset)
    return {"success": True, "data": {"dataset": dataset, "cleared": cleared}}
