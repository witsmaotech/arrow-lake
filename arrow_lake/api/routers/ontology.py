"""v1.11.0 MS1 (F1.4/W2.3): ontology admin API — versions + rules.

* ``GET  /api/v1/ontology/versions`` — version chains (filter by scope /
  template); list view omits the Turtle payload.
* ``GET  /api/v1/ontology/versions/{id}`` — full snapshot incl. Turtle.
* ``GET  /api/v1/ontology/versions/{id}/diff`` — structured diff vs the
  previous version (null for the first version).
* Rules CRUD + state-machine transitions over ``OntologyRulesStore``
  (rules are registered here, NOT executed — MS3 lands the evaluator).

All endpoints are ADMIN (plan W2.3); 503 when system_db is disabled.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import require_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/ontology", tags=["ontology"])


def _version_store(request: Request):
    return getattr(request.app.state, "ontology_store", None)


def _rules_store(request: Request):
    return getattr(request.app.state, "ontology_rules_store", None)


def _require_store(store, name: str):
    if store is None:
        raise HTTPException(
            status_code=503,
            detail=f"system_db disabled; {name} unavailable",
        )
    return store


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------


@router.get("/versions", dependencies=[Depends(require_role(Role.ADMIN))])
async def list_versions(
    request: Request,
    scope: str | None = Query(default=None, description="dataset scope filter"),
    template_name: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """List ontology version chains (newest first, Turtle omitted)."""
    store = _require_store(_version_store(request), "ontology versions")
    items = store.list_versions(scope=scope, template_name=template_name, limit=limit)
    return {"success": True, "data": items, "count": len(items)}


@router.get("/versions/{version_id}", dependencies=[Depends(require_role(Role.ADMIN))])
async def get_version(version_id: int, request: Request) -> dict:
    """Full version row including the Turtle shapes payload."""
    store = _require_store(_version_store(request), "ontology versions")
    row = store.get_version(version_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"version {version_id} not found")
    return {"success": True, "data": row}


@router.get("/versions/{version_id}/diff", dependencies=[Depends(require_role(Role.ADMIN))])
async def get_version_diff(version_id: int, request: Request) -> dict:
    """Structured diff vs the previous version (null on the first version)."""
    store = _require_store(_version_store(request), "ontology versions")
    row = store.get_version(version_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"version {version_id} not found")
    return {"success": True, "data": row.get("diff")}


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------


class RuleUpsertRequest(BaseModel):
    rule_id: str = Field(..., min_length=1, max_length=200,
                         description="business rule id, e.g. GAS.LEAK.R001")
    scope: str = Field(..., min_length=1, max_length=200,
                       description="dataset name or '*' (global)")
    condition_expr: str = Field(..., min_length=1,
                                description="condition expression (registered, evaluated in MS3)")
    conclusion: str = Field(..., min_length=1)
    source_ref: str = Field(..., min_length=1,
                            description="provenance, incl. standard/guobiao version")
    # v1.11.1 W1.4 (DR15 D-2): omitted → insert falls back to defaults,
    # update keeps current values (store resolves).
    rule_type: Literal[
        "validation", "computation", "derivation", "transformation", "risk_control"
    ] | None = Field(default=None, description="five-way rule classification")
    version: str | None = Field(default=None, min_length=1, max_length=32,
                                description="independent rule version, e.g. '1.2'")


@router.get("/rules", dependencies=[Depends(require_role(Role.ADMIN))])
async def list_rules(
    request: Request,
    scope: str | None = Query(default=None),
    status: str | None = Query(default=None),
    rule_type: Literal[
        "validation", "computation", "derivation", "transformation", "risk_control"
    ] | None = Query(default=None),
) -> dict:
    """List ontology rules (filter by scope / status / rule_type)."""
    store = _require_store(_rules_store(request), "ontology rules")
    items = store.list_rules(scope=scope, status=status, rule_type=rule_type)
    return {"success": True, "data": items, "count": len(items)}


@router.post("/rules", status_code=201, dependencies=[Depends(require_role(Role.ADMIN))])
async def upsert_rule(req: RuleUpsertRequest, request: Request) -> dict:
    """Create or update a rule (updates never touch status — transitions only)."""
    store = _require_store(_rules_store(request), "ontology rules")
    store.upsert_rule(
        req.rule_id,
        scope=req.scope,
        condition_expr=req.condition_expr,
        conclusion=req.conclusion,
        source_ref=req.source_ref,
        rule_type=req.rule_type,
        version=req.version,
    )
    rule = store.get_rule(req.rule_id)
    return {"success": True, "data": rule}


@router.post(
    "/rules/{rule_id}/transition",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def transition_rule(
    rule_id: str,
    request: Request,
    to_status: str = Query(description="target status (draft/active/retired)"),
) -> dict:
    """Move a rule along the state machine (draft→active→retired→draft)."""
    store = _require_store(_rules_store(request), "ontology rules")
    try:
        moved = store.transition(rule_id, to_status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not moved:
        raise HTTPException(status_code=404, detail=f"rule {rule_id!r} not found")
    return {"success": True, "data": store.get_rule(rule_id)}


@router.delete("/rules/{rule_id}", dependencies=[Depends(require_role(Role.ADMIN))])
async def delete_rule(rule_id: str, request: Request) -> dict:
    """Delete a rule entirely (prefer retiring via transition for audit)."""
    store = _require_store(_rules_store(request), "ontology rules")
    if not store.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail=f"rule {rule_id!r} not found")
    return {"success": True, "data": {"rule_id": rule_id, "deleted": True}}
