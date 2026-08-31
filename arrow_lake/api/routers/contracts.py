"""Dataset contract management API (DR13/DR14, v1.11.0.1 W4.1).

All endpoints are ADMIN (plan W4.1); 503 when system_db is disabled.
Conventions follow the ontology router (v1.11.0 W2.3).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import audit_write, get_lake, require_role

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


class ContractParseRequest(BaseModel):
    contract_yaml: str = Field(min_length=1, max_length=262_144)


@router.post("/parse", dependencies=[Depends(require_role(Role.ADMIN))])
async def parse_contract_yaml(req: ContractParseRequest) -> dict:
    """建模工作台支撑(2026-08-31):契约 YAML → 结构化 JSON(表单回填用)。

    复用服务端权威解析器(``parse_contract``+compile 校验);浏览器侧
    零 YAML 依赖。解析/编译失败 → 422(工作台"仅校验"也走这里)。
    """
    from arrow_lake.contract.compiler import compile_contract
    from arrow_lake.contract.schema import parse_contract

    try:
        contract = parse_contract(req.contract_yaml)
        compile_contract(contract)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    tables = {}
    for name, sec in contract.tables.items():
        tables[name] = {
            "object_class": sec.object_class,
            "lifecycle": None if sec.lifecycle is None else {
                "column": sec.lifecycle.column,
                "states": list(sec.lifecycle.states),
                "initial": sec.lifecycle.initial,
            },
            "identifier": None if sec.identifier is None else {
                "column": sec.identifier.column,
                "pattern": sec.identifier.pattern,
            },
            "columns": [{
                "name": r.name, "label": r.label, "unit": r.unit,
                "type": r.type, "required": r.required,
                "range": list(r.range) if r.range else None,
                "enum": list(r.enum) if r.enum else None,
            } for r in sec.columns],
        }
    return {
        "dataset": contract.dataset,
        "tables": tables,
        "references": [{
            "from_table": r.from_table, "from_column": r.from_column,
            "to_dataset": r.to_dataset, "to_table": r.to_table,
            "to_column": r.to_column, "cardinality": r.cardinality,
            "kind": r.kind,
        } for r in contract.references],
        "quality": None if contract.quality is None else {
            "critical": contract.quality.critical,
            "drift_kl": contract.quality.drift_kl,
        },
    }


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
async def save_contract(scope: str, req: ContractUpsertRequest, request: Request,
                          user=Depends(require_role(Role.ADMIN))) -> dict:
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
    audit_write(request, "contract.saved", actor=user.sub, dataset=scope,
                payload={"version": rec.get("version"),
                         "created": rec.get("created")})
    return {"scope": scope, **rec}
