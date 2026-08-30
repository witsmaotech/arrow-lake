"""W1.5 — 五维评估端点(v1.11.4 MS5,F5.1 报告面)。

``POST /api/v1/quality/assess/{ds}``(ADMIN):全只读旁路评估——契约
(quality 节)+ 数据集表 + 死信表 + ADL → 五维 → 加权评分/星级/准入/
否决 → 落 ``sys_quality_reports``(V020);audit + lineage best-effort
(失败不阻塞评估,回带 ``*_recorded=false``)。**发布门不是入口门**:
ingest gate(v1.10.7)零触碰,评估只读。

``GET /api/v1/quality/reports/{ds}``(ADMIN):评估历史(newest-first)
+ 最新报告。W1 相关性维度未接线(W2 标注回路)→ 恒降级标记,不造假分。
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.quality.dimensions import (
    DimensionResult,
    annotation_delay_p95_hours,
    compute_accuracy,
    compute_completeness,
    compute_diversity,
    compute_timeliness,
    freshness_hours,
)
from arrow_lake.quality.report import score_dimensions
from arrow_lake.quality.spec import resolve_quality_spec

router = APIRouter(prefix="/api/v1/quality", tags=["quality-gate"])

logger = logging.getLogger(__name__)

_LINEAGE_TIMEOUT = 5.0


def _report_store(request: Request) -> Any:
    store = getattr(request.app.state, "quality_report_store", None)
    if store is None:
        raise HTTPException(
            status_code=503, detail="system_db disabled; quality reports unavailable",
        )
    return store


def _contract_store(request: Request) -> Any:
    store = getattr(request.app.state, "contract_store", None)
    if store is None:
        raise HTTPException(
            status_code=503, detail="system_db disabled; contract registry unavailable",
        )
    return store


def _row_count(lake: Any, name: str) -> int:
    """行数,读不到(表不存在等)→ 0。旁路评估对缺表宽容。"""
    with contextlib.suppress(Exception):
        table = lake.read_dataset(name)
        if table is not None:
            return int(table.num_rows)
    return 0


def _read_table(lake: Any, name: str, *, table: str | None) -> Any:
    """读数据集表;不存在/不可读 → None(调用方区分主表与旁表)。"""
    try:
        return lake.read_dataset(name, table=table)
    except Exception:
        return None


def _spec_snapshot(spec: Any) -> dict[str, Any]:
    return {
        "weights": dict(spec.weights),
        "thresholds": dict(spec.thresholds),
        "vetoes": list(spec.vetoes),
        "admission": list(spec.admission),
        "max_p95_hours": spec.max_p95_hours,
        "critical": spec.critical,
    }


@router.post(
    "/assess/{dataset}",
    status_code=200,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def assess_dataset(
    dataset: str,
    request: Request,
    table: str | None = Query(default=None, description="容器内表名(容器数据集用)"),
    text_column: str | None = Query(
        default=None, description="标注延迟 join 的文本列(缺省跳过延迟指标)",
    ),
    lake=Depends(get_lake),
    user=Depends(require_role(Role.ADMIN)),
) -> dict:
    """触发一次五维评估并落报告(全只读;审计+血缘 best-effort)。"""
    from arrow_lake.contract.schema import parse_contract

    store = _report_store(request)
    contract_store = _contract_store(request)

    try:
        source = lake.read_dataset(dataset, table=table)
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail=f"Dataset '{dataset}' not readable: {exc}",
        ) from exc
    if source is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset}' not found")

    contract = None
    rec = contract_store.get_version(dataset)
    if rec is not None and rec.get("contract_yaml"):
        with contextlib.suppress(ValueError):
            contract = parse_contract(rec["contract_yaml"])

    spec = resolve_quality_spec(contract.quality if contract else None)

    base = table or dataset
    dead_rows = _row_count(lake, f"_{base}_dead_letter")
    adl = _read_table(lake, f"{dataset}_adl", table=None)
    if adl is not None and adl.num_rows == 0:
        adl = None

    now = datetime.now(tz=UTC)
    delay = (
        annotation_delay_p95_hours(source, adl, text_column=text_column)
        if adl is not None and text_column else None
    )
    dimensions: dict[str, DimensionResult] = {
        "relevance": DimensionResult(
            name="relevance", score=None,
            details={"note": "relevance loop lands in W2 (MS5)"},
            source="annotation",
        ),
        "accuracy": compute_accuracy(adl),
        "completeness": compute_completeness(
            source, contract, dead_letter_rows=dead_rows, table_name=table,
        ),
        "diversity": compute_diversity(source),
        "timeliness": compute_timeliness(
            freshness_hours=freshness_hours(source, now=now),
            annotation_delay_p95_hours=delay,
            max_p95_hours=spec.max_p95_hours,
        ),
    }

    report = score_dimensions(dimensions, spec)
    actor = getattr(user, "sub", "system")
    persisted = store.create_report(
        dataset,
        total_score=report.total_score,
        star=report.star,
        admission=report.admission,
        verdict=report.verdict,
        dimensions={
            n: {"score": r.score, "details": r.details, "source": r.source}
            for n, r in report.dimensions.items()
        },
        vetoes=[dict(v) for v in report.vetoes],
        degraded=list(report.degraded),
        spec=_spec_snapshot(spec),
        assessed_by=actor,
    )

    audit_recorded = False
    with contextlib.suppress(Exception):
        lake.audit_record(
            "quality.assess", dataset_name=dataset, actor=actor,
            payload={
                "report_id": persisted.get("id"),
                "total_score": report.total_score, "star": report.star,
                "admission": report.admission, "verdict": report.verdict,
            },
        )
        audit_recorded = True
    lineage_recorded = False
    with contextlib.suppress(Exception):
        from arrow_lake.api.utils import run_sync

        await run_sync(
            lake.lineage_record_event, dataset, "quality.assessed",
            actor=actor,
            metadata={
                "report_id": persisted.get("id"),
                "total_score": report.total_score, "star": report.star,
                "verdict": report.verdict,
            },
            timeout=_LINEAGE_TIMEOUT, label="quality_assess_lineage",
        )
        lineage_recorded = True
    if not audit_recorded or not lineage_recorded:
        logger.warning(
            "quality.assess side-channel degraded (audit=%s lineage=%s)",
            audit_recorded, lineage_recorded,
        )

    return {
        "dataset": dataset,
        "report_id": persisted.get("id"),
        "total_score": report.total_score,
        "star": report.star,
        "admission": report.admission,
        "verdict": report.verdict,
        "vetoes": [dict(v) for v in report.vetoes],
        "degraded": list(report.degraded),
        "dimensions": {
            n: {"score": r.score, "source": r.source}
            for n, r in report.dimensions.items()
        },
        "assessed_at": persisted.get("assessed_at"),
        "audit_recorded": audit_recorded,
        "lineage_recorded": lineage_recorded,
    }


@router.get(
    "/reports/{dataset}",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def list_reports(
    dataset: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """评估历史(newest-first)+ 最新报告。"""
    store = _report_store(request)
    reports = store.list_reports(dataset, limit=limit)
    return {"dataset": dataset, "total": len(reports), "reports": reports,
            "latest": reports[0] if reports else None}
