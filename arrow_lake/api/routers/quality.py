"""Quality filtering and deduplication endpoints."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends, Path, Request

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import authorize_dataset, get_lake, require_role
from arrow_lake.api.models.common import _NAME_PATTERN, arrow_table_to_response
from arrow_lake.api.models.quality import (
    DedupRequest,
    DedupResponse,
    ExtractRequest,
    LlmLabelRequest,
    PrepTaskResponse,
    QualityFilterRequest,
    QualityFilterResponse,
    QualityReportResponse,
    QualityRuleResultItem,
    QualityRuleSetRequest,
    QualityRuleSetResponse,
)
from arrow_lake.api.tasks import TaskManager
from arrow_lake.api.utils import run_sync
from arrow_lake.quality.llm_enrich import extract_fields, label_column

# Strong references for fire-and-forget background tasks so the asyncio GC
# does not silently cancel them mid-run (cf. kg_build fire-forget lesson).
_BG_TASKS: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Schedule a background coroutine, keeping a strong ref until it completes."""
    t = asyncio.create_task(coro)
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)

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
        text_column=req.text_column,
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


@router.get("/{name}/quality/profile")
async def quality_profile(
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.VIEWER)),
) -> dict:
    """Get quality profile for a dataset — column-level statistics and quality scores."""
    from arrow_lake.quality.profiler import QualityProfiler

    table = await run_sync(
        lake.read_dataset, name,
        timeout=_QUALITY_TIMEOUT, label="quality_profile_read",
    )

    profiler = QualityProfiler()
    profile = profiler.profile(table, name)

    columns = []
    for cp in profile.column_profiles:
        col_data = {
            "name": cp.name,
            "dtype": cp.dtype,
            "null_count": cp.null_count,
            "null_percentage": cp.null_percentage,
            "unique_count": cp.unique_count,
            "min_value": cp.min_value,
            "max_value": cp.max_value,
        }
        if cp.histogram is not None:
            col_data["histogram"] = [dict(b) for b in cp.histogram]
        columns.append(col_data)

    return {
        "success": True,
        "data": {
            "dataset_name": profile.dataset_name,
            "total_rows": profile.total_rows,
            "total_columns": profile.total_columns,
            "overall_quality_score": profile.overall_quality_score,
            "profiled_at": profile.profiled_at,
            "columns": columns,
        },
        "error": None,
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Data-prep enrichment (LLM labeling & structured extraction) — async
# ---------------------------------------------------------------------------

_LLM_DEFAULT_MAX_ROWS = 5000


@router.post(
    "/{name}/quality/llm_label",
    response_model=PrepTaskResponse,
    status_code=202,
)
async def llm_label(
    request: Request,  # noqa: ARG001 — present for symmetry / future app-state hooks
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: LlmLabelRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> PrepTaskResponse:
    """Batch-LLM label a text column into a new column (async task).

    Renders ``prompt_template`` (with ``{text}``) per row, writes results to
    ``new_column`` via native Lance column add. Poll ``GET /tasks/{task_id}/status``.
    """
    authorize_dataset(request, name, write=True)
    task_id = TaskManager.create_task(
        "llm_label", name,
        detail={"column": req.column, "new_column": req.new_column},
    )
    _spawn(
        TaskManager.run_background(
            task_id, label_column, lake, name,
            req.column, req.new_column, req.prompt_template,
            model=req.model,
            max_rows=req.max_rows or _LLM_DEFAULT_MAX_ROWS,
            concurrency=req.concurrency,
        )
    )
    return PrepTaskResponse(
        task_id=task_id,
        operation="llm_label",
        message=f"LLM labeling started: '{name}.{req.column}' → '{req.new_column}'",
    )


@router.post(
    "/{name}/quality/extract",
    response_model=PrepTaskResponse,
    status_code=202,
)
async def extract(
    request: Request,  # noqa: ARG001
    name: str = Path(..., pattern=_NAME_PATTERN),
    *,
    req: ExtractRequest,
    lake=Depends(get_lake),
    _user: dict = Depends(require_role(Role.EDITOR)),
) -> PrepTaskResponse:
    """Batch structured extraction from a text column → multiple columns (async).

    Each field in ``fields`` becomes a new string column. Poll task status.
    """
    authorize_dataset(request, name, write=True)
    field_dicts = [f.model_dump() for f in req.fields]
    task_id = TaskManager.create_task(
        "extract", name,
        detail={"column": req.column, "fields": [f["name"] for f in field_dicts]},
    )
    _spawn(
        TaskManager.run_background(
            task_id, extract_fields, lake, name,
            req.column, field_dicts,
            model=req.model,
            max_rows=req.max_rows or _LLM_DEFAULT_MAX_ROWS,
            concurrency=req.concurrency,
        )
    )
    return PrepTaskResponse(
        task_id=task_id,
        operation="extract",
        message=f"Structured extraction started on '{name}.{req.column}' ({len(field_dicts)} fields)",
    )
