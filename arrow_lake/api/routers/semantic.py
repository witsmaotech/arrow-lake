"""v1.11.1 MS2 语义对齐 API(W3.3,F2.2)。

* ``GET  /api/v1/semantic/units`` —— 单位注册表只读(VIEWER;查询侧拼
  投影前的口径参考)。
* 对齐配置 CRUD(ADMIN,沿 contracts 惯例:解析先行 422 / dataset==scope /
  同 hash 跳过);保存时对契约做**软校验**(warnings 回带,不阻塞)并写
  lineage 事件(``semantic_alignment``,dataset 级;失败不阻塞保存,
  ``lineage_recorded=false`` 回带)。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import require_role
from arrow_lake.api.utils import run_sync

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/semantic", tags=["semantic"])

_LINEAGE_TIMEOUT = 30


def _store(request: Request):
    return getattr(request.app.state, "semantic_alignment_store", None)


def _require_store(store, name: str):
    if store is None:
        raise HTTPException(
            status_code=503, detail=f"system_db disabled; {name} unavailable",
        )
    return store


# ---------------------------------------------------------------------------
# 单位注册表(只读)
# ---------------------------------------------------------------------------


@router.get("/units", dependencies=[Depends(require_role(Role.VIEWER))])
async def list_units() -> dict:
    """Unit registry (dimension → unit → affine {factor, offset})."""
    from arrow_lake.semantic.units import registry_listing

    return {"success": True, "data": {"dimensions": registry_listing()}}


# ---------------------------------------------------------------------------
# 对齐配置 CRUD
# ---------------------------------------------------------------------------


class AlignmentUpsertRequest(BaseModel):
    alignment_yaml: str = Field(min_length=1, max_length=200_000)


@router.get("/alignments", dependencies=[Depends(require_role(Role.ADMIN))])
async def list_alignments(request: Request) -> dict:
    store = _require_store(_store(request), "semantic alignments")
    scopes = store.list_scopes()
    return {"total": len(scopes), "alignments": scopes}


@router.get("/alignments/{scope}", dependencies=[Depends(require_role(Role.ADMIN))])
async def get_latest(scope: str, request: Request) -> dict:
    store = _require_store(_store(request), "semantic alignments")
    rec = store.get_version(scope)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No alignment for '{scope}'")
    return {"scope": scope, **rec}


@router.get("/alignments/{scope}/versions", dependencies=[Depends(require_role(Role.ADMIN))])
async def list_versions(scope: str, request: Request) -> dict:
    store = _require_store(_store(request), "semantic alignments")
    versions = store.list_versions(scope)
    return {"scope": scope, "total": len(versions), "versions": versions}


@router.put("/alignments/{scope}", dependencies=[Depends(require_role(Role.ADMIN))])
async def save_alignment(
    scope: str, req: AlignmentUpsertRequest, request: Request,
    user=Depends(require_role(Role.ADMIN)),
) -> dict:
    """Save an alignment config: parse-first (422), soft-check against the
    dataset contract (warnings, non-blocking), store the next version
    (same-hash skips), record a lineage event (best-effort)."""
    store = _require_store(_store(request), "semantic alignments")
    from arrow_lake.semantic.alignment import (
        check_against_contract,
        parse_alignment,
    )

    try:
        parsed = parse_alignment(req.alignment_yaml)
    except Exception as exc:  # surface as 422 (contracts惯例)
        raise HTTPException(status_code=422, detail=f"Invalid alignment: {exc}") from exc
    if parsed.dataset != scope:
        raise HTTPException(
            status_code=422,
            detail=f"Alignment 'dataset' field ({parsed.dataset!r}) must match "
                   f"scope ({scope!r})",
        )

    warnings: list[dict] = []
    contract_store = getattr(request.app.state, "contract_store", None)
    if contract_store is not None:
        from arrow_lake.contract.schema import parse_contract

        latest = contract_store.get_version(scope)
        if latest is not None:
            try:
                warnings = check_against_contract(
                    parse_contract(latest["contract_yaml"]), parsed,
                )
            except Exception:  # 软校验不因契约解析失败阻塞
                logger.warning("alignment_soft_check_failed", exc_info=True)

    rec = store.save_alignment(scope, req.alignment_yaml)

    lineage_recorded = False
    lake = getattr(request.app.state, "lake", None)
    if lake is not None and rec["created"]:
        try:
            await run_sync(
                lake.lineage_record_event, scope, "semantic_alignment",
                transform_type="semantic_alignment",
                actor=getattr(user, "sub", "semantic-api"),
                metadata={"version": rec["version"],
                          "source_hash": rec["source_hash"]},
                timeout=_LINEAGE_TIMEOUT, label="semantic_alignment_lineage",
            )
            lineage_recorded = True
        except Exception:  # 审计侧车不阻塞主写
            logger.warning("semantic_alignment_lineage_failed", exc_info=True)
    return {"scope": scope, **rec, "warnings": warnings,
            "lineage_recorded": lineage_recorded}


@router.delete("/alignments/{scope}", dependencies=[Depends(require_role(Role.ADMIN))])
async def delete_alignment(scope: str, request: Request) -> dict:
    store = _require_store(_store(request), "semantic alignments")
    if not store.delete_scope(scope):
        raise HTTPException(status_code=404, detail=f"No alignment for '{scope}'")
    return {"scope": scope, "deleted": True}
