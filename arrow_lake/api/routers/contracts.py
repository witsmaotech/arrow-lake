"""Dataset contract management API (DR13/DR14, v1.11.0.1 W4.1).

All endpoints are ADMIN (plan W4.1); 503 when system_db is disabled.
Conventions follow the ontology router (v1.11.0 W2.3).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import require_role

router = APIRouter(prefix="/api/v1/contracts", tags=["contracts"])


def _store(request: Request):
    return getattr(request.app.state, "contract_store", None)


def _require_store(store: Any) -> Any:
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Contract registry unavailable (system_db disabled)",
        )
    return store


class ContractUpsertRequest(BaseModel):
    """Save a contract (new version when content changed; same-hash skips)."""

    contract_yaml: str = Field(min_length=1, max_length=200_000)


@router.get("", dependencies=[Depends(require_role(Role.ADMIN))])
async def list_contracts(request: Request) -> dict:
    """List contract scopes with their latest version summary."""
    store = _require_store(_store(request))
    scopes = store.list_scopes()
    return {
        "total": len(scopes),
        "contracts": [
            {
                "scope": s["scope"],
                "version": s["version"],
                "source_hash": s["source_hash"],
                "updated_at": s["created_at"],
            }
            for s in scopes
        ],
    }


@router.get("/{scope}/versions", dependencies=[Depends(require_role(Role.ADMIN))])
async def list_versions(scope: str, request: Request) -> dict:
    """Version chain for one contract scope, newest first."""
    store = _require_store(_store(request))
    versions = store.list_versions(scope)
    return {"scope": scope, "total": len(versions), "versions": versions}


@router.get("/{scope}/versions/{version}", dependencies=[Depends(require_role(Role.ADMIN))])
async def get_version(scope: str, version: int, request: Request) -> dict:
    """One contract version including the YAML payload."""
    store = _require_store(_store(request))
    rec = store.get_version(scope, version=version)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No contract version {version} for '{scope}'")
    return {"scope": scope, **rec}


@router.get("/{scope}", dependencies=[Depends(require_role(Role.ADMIN))])
async def get_latest(scope: str, request: Request) -> dict:
    """Latest contract version including the YAML payload."""
    store = _require_store(_store(request))
    rec = store.get_version(scope)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No contract for '{scope}'")
    return {"scope": scope, **rec}


@router.get("/{scope}/diff", dependencies=[Depends(require_role(Role.ADMIN))])
async def get_latest_diff(scope: str, request: Request) -> dict:
    """Structured diff of the latest version vs its predecessor."""
    store = _require_store(_store(request))
    rec = store.get_version(scope)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No contract for '{scope}'")
    return {"scope": scope, "version": rec["version"], "diff": rec["diff"]}


@router.put("/{scope}", status_code=200, dependencies=[Depends(require_role(Role.ADMIN))])
async def save_contract(scope: str, req: ContractUpsertRequest, request: Request) -> dict:
    """Save a contract: validates by parsing first, then stores the next
    version (same content hash → no new version, created=False)."""
    store = _require_store(_store(request))
    from arrow_lake.contract.schema import parse_contract

    try:
        parsed = parse_contract(req.contract_yaml)
    except Exception as exc:  # noqa: BLE001 — surface as 422
        raise HTTPException(status_code=422, detail=f"Invalid contract: {exc}") from exc
    if parsed.dataset != scope:
        raise HTTPException(
            status_code=422,
            detail=f"Contract 'dataset' field ({parsed.dataset!r}) must match scope ({scope!r})",
        )
    rec = store.save_contract(scope, req.contract_yaml)
    return {"scope": scope, **rec}
