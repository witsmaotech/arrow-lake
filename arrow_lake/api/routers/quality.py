"""Quality filtering and deduplication endpoints."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Path

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.api.models.common import _NAME_PATTERN, arrow_table_to_response
from arrow_lake.api.models.quality import (
    DedupRequest,
    DedupResponse,
    QualityFilterRequest,
    QualityFilterResponse,
    QualityRuleResultItem,
    QualityRuleSetRequest,
    QualityRuleSetResponse,
    QualityReportResponse,
)
from arrow_lake.api.utils import run_sync

router = APIRouter(prefix="/api/v1/datasets", tags=["quality"])

_QUALITY_TIMEOUT = 300


@router.post("/{name}/quality/filter", response_model=QualityFilterResponse)
async def quality_filter(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: QualityFilterRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> QualityFilterResponse:
    """Run quality filters on a dataset."""
    report = await run_sync(
        lake.quality_filter, name, req.active_filters, mode=req.mode,
        timeout=_QUALITY_TIMEOUT, label="quality_filter",
    )
    return QualityFilterResponse(report=asdict(report) if hasattr(report, "__dataclass_fields__") else report)


@router.get("/{name}/quality/report", response_model=QualityReportResponse)
async def quality_report(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> QualityReportResponse:
    """Get quality report for a dataset (runs filters with default config)."""
    report = await run_sync(
        lake.quality_filter, name,
        timeout=_QUALITY_TIMEOUT, label="quality_report",
    )
    return QualityReportResponse(report=report.to_json())


@router.post("/{name}/quality/deduplicate", response_model=DedupResponse)
async def deduplicate(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: DedupRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> DedupResponse:
    """Run content deduplication on a dataset."""
    report = await run_sync(
        lake.deduplicate, name,
        strategy=req.strategy, action=req.action,
        perceptual_threshold=req.perceptual_threshold,
        timeout=_QUALITY_TIMEOUT, label="deduplicate",
    )
    report_dict = asdict(report) if hasattr(report, "__dataclass_fields__") else report
    table_resp = arrow_table_to_response(report.table, "json")
    report_dict["table"] = table_resp
    return DedupResponse(report=report_dict)


@router.post("/{name}/quality/rules", response_model=QualityRuleSetResponse)
async def quality_rules(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: QualityRuleSetRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> QualityRuleSetResponse:
    """Apply declarative quality rules to a dataset.

    Supports length, range, regex, and duplicate checks with
    reject, flag, or remove actions.
    """
    from arrow_lake.quality.rules import QualityRuleEngine, RuleDefinition

    table = await run_sync(
        lake.read_dataset, name,
        timeout=_QUALITY_TIMEOUT, label="quality_rules_read",
    )

    engine = QualityRuleEngine()
    for rule_req in req.rules:
        engine.add_rule(RuleDefinition(
            name=rule_req.name,
            column=rule_req.column,
            check=rule_req.check,
            params=rule_req.params,
            action=rule_req.action,
            message=rule_req.message,
        ))

    results = engine.evaluate(table)
    total_affected = sum(r.affected_count for r in results)

    return QualityRuleSetResponse(
        applied_rules=len(engine.rules),
        results=[
            QualityRuleResultItem(
                rule_name=r.rule_name,
                action=r.action,
                affected_count=r.affected_count,
                message=r.message,
            )
            for r in results
        ],
        total_affected_rows=total_affected,
    )
