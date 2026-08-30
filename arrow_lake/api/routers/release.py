"""W3.2 — 发布端点(v1.11.4 MS5,F5.4 发布门)。

``POST /release/{ds}``(ADMIN):**发布门校验链**(设计 §6)——
最新质量报告存在 → 准入(evaluate_admission:否决/below_bronze/劣化)
→ 漂移超限拒 → ``?force`` 不可绕(reason 必填,audit ``release.forced``)
→ 成功:锁 Lance 版本 + 语义化 tag(默认 MINOR)+ datasheet 存档 +
漂移基线自动快照(source=release,§5)+ audit/lineage ``release.published``。

``GET /release/{ds}`` 历史;``POST /release/{ds}/retire`` 软下线;
``GET /release/{ds}/datasheet`` YAML 导出(默认最新 active,``?tag=`` 指定)。
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.routers.quality_report import _load_contract_and_spec
from arrow_lake.quality.drift import evaluate_drift, snapshot_table
from arrow_lake.quality.report import ScoredReport, evaluate_admission
from arrow_lake.release.datasheet import build_datasheet, datasheet_yaml
from arrow_lake.release.registry import format_tag, next_tag, parse_tag

router = APIRouter(prefix="/api/v1/release", tags=["release"])

logger = logging.getLogger(__name__)


class ReleaseRequest(BaseModel):
    changelog: str = Field(min_length=1, max_length=4000)
    bump: Literal["major", "minor", "patch"] = "minor"
    force: bool = False
    reason: str | None = Field(
        default=None, max_length=2000,
        description="force 覆盖必填(审计留痕)",
    )
    category: str | None = Field(
        default=None, max_length=64, description="规格书 category(缺省省略)",
    )


class RetireRequest(BaseModel):
    tag: str


def _store(request: Request, name: str, what: str) -> Any:
    store = getattr(request.app.state, name, None)
    if store is None:
        raise HTTPException(
            status_code=503, detail=f"system_db disabled; {what} unavailable",
        )
    return store


def _read_source(lake: Any, dataset: str, table: str | None) -> Any:
    try:
        source = lake.read_dataset(dataset, table=table)
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail=f"Dataset '{dataset}' not readable: {exc}",
        ) from exc
    if source is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset}' not found")
    return source


@router.post(
    "/{dataset}",
    status_code=200,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def create_release(
    dataset: str,
    req: ReleaseRequest,
    request: Request,
    table: str | None = Query(default=None, description="容器内表名"),
    lake=Depends(get_lake),
    user=Depends(require_role(Role.ADMIN)),
) -> dict:
    """发布:校验链 → 锁版本+tag+规格书 → 基线快照 → 审计/血缘。"""
    store = _store(request, "release_store", "release registry")
    qstore = _store(request, "quality_report_store", "quality reports")
    dstore = _store(request, "drift_baseline_store", "drift baselines")
    cstore = _store(request, "contract_store", "contract registry")

    source = _read_source(lake, dataset, table)

    report = qstore.latest_report(dataset)
    if report is None:
        raise HTTPException(
            status_code=422,
            detail=f"no quality report for '{dataset}' — run /quality/assess first",
        )

    contract, spec = _load_contract_and_spec(cstore, dataset)

    # 漂移超限拒(基线存在才比较;发布成功后刷新基线)
    drift_info: dict[str, Any] | None = None
    baseline = dstore.get_baseline(dataset)
    if baseline is not None:
        drift_info = evaluate_drift(
            source, baseline.get("columns", {}), spec.drift_kl)

    # 准入:否决 / below_bronze / 拒绝劣化(基准 = 最新 active 发布)
    latest_active = store.latest_release(dataset)
    previous_total = (
        latest_active["total_score"] if latest_active else None)
    decision = evaluate_admission(
        ScoredReport(
            dimensions={},
            total_score=report.get("total_score"),
            star=report.get("star") or 0,
            admission=report.get("admission") or "none",
            verdict=report.get("verdict") or "pass",
            vetoes=tuple(report.get("vetoes") or []),
            degraded=tuple(report.get("degraded") or []),
        ),
        previous_total=previous_total,
    )
    reasons = list(decision.reasons)
    if drift_info and drift_info["drifted"]:
        reasons.extend(f"drift:{c}" for c in drift_info["drifted"])

    actor = getattr(user, "sub", "system")
    forced = bool(reasons)
    if reasons:
        if not req.force:
            raise HTTPException(
                status_code=422,
                detail={
                    "blocked": reasons, "decision": decision.tier,
                    "hint": "force=true + reason to override (audited)",
                },
            )
        if not (req.reason or "").strip():
            raise HTTPException(
                status_code=422, detail="force requires a reason",
            )

    # tag:对全部历史(含 retired)取最大号再 bump
    prev_any = store.latest_release(dataset, active_only=False)
    latest_version = parse_tag(prev_any["tag"]) if prev_any else None
    tag = format_tag(next_tag(latest_version, bump=req.bump))

    # Lance 版本锁定(发布时刻快照号)
    try:
        lance_version = int(
            lake._get_storage().open_dataset(dataset, table=table).version)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"cannot lock Lance version for '{dataset}': {exc}",
        ) from exc

    adl = None
    with contextlib.suppress(Exception):
        adl = lake.read_dataset(f"{dataset}_adl")

    datasheet = build_datasheet(
        dataset=dataset, tag=tag, lance_version=lance_version, report=report,
        contract=contract, table=source, adl=adl, changelog=req.changelog,
        released_by=actor, released_at=datetime.now(tz=UTC).isoformat(),
        category=req.category,
    )
    rec = store.create_release(
        dataset=dataset, tag=tag, lance_version=lance_version,
        changelog=req.changelog, quality_report_id=report.get("id"),
        total_score=report.get("total_score"), star=report.get("star"),
        admission=report.get("admission"),
        datasheet_yaml=datasheet_yaml(datasheet), released_by=actor,
    )
    if rec is None:
        raise HTTPException(
            status_code=422, detail=f"release tag '{tag}' already exists",
        )

    # 发布时自动快照基线(设计 §5);best-effort 不阻塞发布
    with contextlib.suppress(Exception):
        dstore.set_baseline(dataset, snapshot_table(source), source="release")

    with contextlib.suppress(Exception):
        lake.audit_record(
            "release.published", dataset_name=dataset, actor=actor,
            payload={
                "tag": tag, "lance_version": lance_version,
                "total_score": report.get("total_score"),
                "forced": forced,
            },
        )
    if forced:
        with contextlib.suppress(Exception):
            lake.audit_record(
                "release.forced", dataset_name=dataset, actor=actor,
                payload={"tag": tag, "reason": req.reason,
                         "overridden": reasons},
            )
    with contextlib.suppress(Exception):
        from arrow_lake.api.utils import run_sync

        await run_sync(
            lake.lineage_record_event, dataset, "release.published",
            actor=actor,
            metadata={
                "tag": tag, "lance_version": lance_version,
                "total_score": report.get("total_score"),
            },
            timeout=5.0, label="release_lineage",
        )

    return {
        **rec, "datasheet": datasheet,
        "forced": forced, "overridden_reasons": reasons if forced else [],
    }


@router.get(
    "/{dataset}",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def release_history(dataset: str, request: Request) -> dict:
    """发布历史(newest-first)+ 最新 active。"""
    store = _store(request, "release_store", "release registry")
    releases = store.list_releases(dataset)
    return {"dataset": dataset, "total": len(releases), "releases": [
        {k: v for k, v in r.items() if k != "datasheet_yaml"}
        for r in releases
    ], "latest_active": (
        {k: v for k, v in store.latest_release(dataset).items()
         if k != "datasheet_yaml"}
        if store.latest_release(dataset) else None)}


@router.post(
    "/{dataset}/retire",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def retire_release(
    dataset: str, req: RetireRequest, request: Request,
    lake=Depends(get_lake), user=Depends(require_role(Role.ADMIN)),
) -> dict:
    """软下线(active→retired;历史保留)。"""
    store = _store(request, "release_store", "release registry")
    if not store.retire_release(dataset, req.tag):
        raise HTTPException(
            status_code=404,
            detail=f"no active release '{dataset}@{req.tag}'",
        )
    actor = getattr(user, "sub", "system")
    with contextlib.suppress(Exception):
        lake.audit_record(
            "release.retired", dataset_name=dataset, actor=actor,
            payload={"tag": req.tag},
        )
    return {"dataset": dataset, "tag": req.tag, "status": "retired"}


@router.get(
    "/{dataset}/datasheet",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def export_datasheet(
    dataset: str,
    request: Request,
    tag: str | None = Query(default=None, description="缺省最新 active"),
) -> Response:
    """规格书 YAML 导出(存档生成物,S9)。"""
    store = _store(request, "release_store", "release registry")
    if tag is not None:
        rec = store.get_release(dataset, tag)
    else:
        rec = store.latest_release(dataset)
    if rec is None:
        raise HTTPException(
            status_code=404, detail=f"no release for '{dataset}'"
            + (f" with tag {tag}" if tag else ""),
        )
    return Response(
        content=rec["datasheet_yaml"], media_type="application/yaml",
        headers={"Content-Disposition":
                 f'attachment; filename="{dataset}-{rec["tag"]}-datasheet.yaml"'},
    )
