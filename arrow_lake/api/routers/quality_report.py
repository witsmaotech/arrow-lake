"""五维评估/漂移/相关性回路端点(v1.11.4 MS5 W1-W2,F5.1/F5.2/F5.3)。

* ``POST /quality/assess/{ds}``(W1.5):全只读旁路评估——契约(quality
  节)+ 数据集表 + 死信表 + ADL → 五维 → 加权评分/星级/准入/否决 → 落
  ``sys_quality_reports``(V020);audit + lineage best-effort;漂移随评估
  触发(W2)。**发布门不是入口门**:ingest gate(v1.10.7)零触碰。
* ``GET /quality/reports/{ds}``:评估历史(newest-first)+ 最新报告。
* ``POST /quality/drift/{ds}``(W2.3):无基线 → 自动落基线;``reset``
  → 重置;否则逐列 KL + metrics Gauge(阈值 = 契约 ``quality.drift_kl``,
  缺省 0.1)。
* ``POST /quality/relevance/{ds}``(W2.1):相关性回路启动(抽样 → 三分类
  项目 → LS 派发/LLM 直评降级,202 后台);回收由既有 scheduler 承担。
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role
from arrow_lake.core.metrics import quality_drift_kl
from arrow_lake.quality.dimensions import (
    DimensionResult,
    annotation_delay_p95_hours,
    compute_accuracy,
    compute_completeness,
    compute_diversity,
    compute_relevance,
    compute_timeliness,
    freshness_hours,
)
from arrow_lake.quality.drift import evaluate_drift, snapshot_table
from arrow_lake.quality.relevance import (
    RELEVANCE_LS_CONFIG,
    RELEVANCE_SAMPLE_CAP,
    dispatch_relevance,
    llm_only_relevance,
    relevance_project_name,
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


def _drift_store(request: Request) -> Any:
    store = getattr(request.app.state, "drift_baseline_store", None)
    if store is None:
        raise HTTPException(
            status_code=503, detail="system_db disabled; drift baselines unavailable",
        )
    return store


def _load_contract_and_spec(
    contract_store: Any, dataset: str,
) -> tuple[Any, Any]:
    """最新契约(解析失败宽容视无)+ 生效 quality 配置。"""
    from arrow_lake.contract.schema import parse_contract

    contract = None
    rec = contract_store.get_version(dataset)
    if rec is not None and rec.get("contract_yaml"):
        with contextlib.suppress(ValueError):
            contract = parse_contract(rec["contract_yaml"])
    return contract, resolve_quality_spec(contract.quality if contract else None)


def _drift_scan(
    *, dataset: str, source: Any, store: Any, spec: Any,
) -> dict[str, Any]:
    """无基线 → 落基线(首次 assess 自动,风险表口径);有 → 逐列 KL。

    KL 写 metrics Gauge;逐列评估复用 ``drift.evaluate_drift``(发布层
    超限拒同款,W3)。
    """
    baseline = store.get_baseline(dataset)
    snap = snapshot_table(source)
    if baseline is None:
        store.set_baseline(dataset, snap, source="assess")
        return {"status": "baseline_created", "columns": len(snap)}

    result = evaluate_drift(source, baseline.get("columns", {}), spec.drift_kl)
    for name, info in result["columns"].items():
        quality_drift_kl.labels(dataset=dataset, column=name).set(info["kl"])
    return {
        "status": "compared", "threshold": spec.drift_kl,
        "baseline_id": baseline.get("id"),
        "baseline_at": baseline.get("created_at"),
        **result,
    }


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

    contract, spec = _load_contract_and_spec(contract_store, dataset)

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
        "relevance": compute_relevance(adl),
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

    # 漂移随评估跑(设计 §5:发布/手动/评估触发;首次自动落基线)
    drift: dict[str, Any] | None = None
    dstore = getattr(request.app.state, "drift_baseline_store", None)
    if dstore is not None:
        with contextlib.suppress(Exception):
            drift = _drift_scan(
                dataset=dataset, source=source, store=dstore, spec=spec)

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
        "drift": drift,
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


@router.post(
    "/drift/{dataset}",
    status_code=200,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def detect_drift(
    dataset: str,
    request: Request,
    table: str | None = Query(default=None, description="容器内表名(容器数据集用)"),
    reset: bool = Query(default=False, description="true=以当前数据重置基线"),
    lake=Depends(get_lake),
    user=Depends(require_role(Role.ADMIN)),
) -> dict:
    """漂移检测:无基线 → 自动落基线;``reset`` → 重置;否则逐列 KL。"""
    store = _drift_store(request)
    contract_store = _contract_store(request)

    try:
        source = lake.read_dataset(dataset, table=table)
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail=f"Dataset '{dataset}' not readable: {exc}",
        ) from exc
    if source is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset}' not found")

    _, spec = _load_contract_and_spec(contract_store, dataset)
    if reset:
        snap = snapshot_table(source)
        rec = store.set_baseline(dataset, snap, source="manual")
        return {"dataset": dataset, "status": "baseline_reset",
                "baseline_id": rec.get("id"), "columns": len(snap)}

    result = _drift_scan(dataset=dataset, source=source, store=store, spec=spec)
    return {"dataset": dataset, **result}


@router.post(
    "/relevance/{dataset}",
    status_code=202,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def start_relevance_loop(
    dataset: str,
    request: Request,
    n: int = Query(default=RELEVANCE_SAMPLE_CAP, ge=1, le=RELEVANCE_SAMPLE_CAP),
    text_column: str = Query(default="text"),
    llm_only: bool = Query(
        default=False,
        description="true=跳过 LS 人工复核,LLM 直评写 ADL(降级档)",
    ),
    lake=Depends(get_lake),
    user=Depends(require_role(Role.ADMIN)),
) -> dict:
    """相关性回路启动(F5.2):抽样 → relevance 项目 → 派发(后台 202)。

    人路:LS 三分类项目 + LLM 预标注,回收由既有 30s scheduler 自动
    (零新回收设施);降级:``llm_only`` 或 LS 未配置时 LLM 直评写 ADL
    (annotator_id=``llm:<model>``,报告侧 source=llm 标非人工结论)。
    """
    import random as _random

    astore = getattr(request.app.state, "annotation_project_store", None)
    if astore is None:
        raise HTTPException(
            status_code=503, detail="system_db disabled; annotation registry unavailable",
        )

    try:
        source = lake.read_dataset(dataset)
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail=f"Dataset '{dataset}' not readable: {exc}",
        ) from exc
    if source is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset}' not found")
    if text_column not in source.column_names:
        raise HTTPException(
            status_code=422,
            detail=f"text column '{text_column}' not in dataset '{dataset}'",
        )
    rows = source.to_pylist()
    if not rows:
        raise HTTPException(status_code=422, detail=f"dataset '{dataset}' is empty")
    sample = _random.sample(rows, min(n, len(rows)))

    name = relevance_project_name(dataset)
    rec = astore.get_project(name)
    if rec is None:
        rec = astore.create_project(
            name=name, dataset=dataset, template_name="relevance",
            labeling_config=RELEVANCE_LS_CONFIG, config_source="generated",
        )
    if rec.get("status") != "active":
        raise HTTPException(status_code=422, detail=f"project '{name}' is closed")

    provider = getattr(request.app.state, "relevance_provider", None)
    if provider is None:
        with contextlib.suppress(Exception):
            llm_cfg = getattr(getattr(lake, "_config", None), "llm", None)
            if llm_cfg is not None:
                from arrow_lake.rag.provider import create_llm_provider

                provider = create_llm_provider(llm_cfg)

    aconf = getattr(request.app.state, "config", None)
    aconf = getattr(aconf, "annotation", None) if aconf is not None else None
    ls_ready = bool(
        getattr(aconf, "ls_url", None) and getattr(aconf, "ls_api_token", None))
    mode = "ls"
    if llm_only or not ls_ready:
        if provider is None:
            raise HTTPException(
                status_code=503,
                detail="relevance loop unavailable: LS not configured and no LLM provider",
            )
        mode = "llm_only"
        if not llm_only:
            mode = "llm_only(auto: LS not configured)"
        work = llm_only_relevance(
            lake=lake, dataset=dataset, rows=sample,
            text_column=text_column, provider=provider,
        )
    else:
        from arrow_lake.annotation.dispatch import LSClient

        work = dispatch_relevance(
            ls_client=LSClient(aconf.ls_url, aconf.ls_api_token),
            project_title=name, rows=sample, text_column=text_column,
            provider=provider, ls_project_id=rec.get("ls_project_id"),
            bind_ls_project=lambda pid: astore.set_ls_project_id(name, pid),
        )

    from arrow_lake.api.tasks import spawn_background

    spawn_background(work)
    actor = getattr(user, "sub", "system")
    with contextlib.suppress(Exception):
        lake.audit_record(
            "quality.relevance", dataset_name=dataset, actor=actor,
            payload={"project": name, "mode": mode, "sampled": len(sample)},
        )
    return {
        "project": name, "mode": mode, "sampled": len(sample),
        "status": "accepted",
        "ls_project_id": rec.get("ls_project_id"),
        "note": ("LLM 直评为建议非结论(降级档)"
                 if mode.startswith("llm_only") else None),
    }
