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
from pydantic import BaseModel, Field

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
    rules_json: str | None = Query(
        default=None,
        description='L2 泛化规则 JSON [[regex, replacement],...](LLM 外发/LS 文本脱敏)',
    ),
    entities_json: str | None = Query(
        default=None, description="L3 假名实体名 JSON 数组",
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

    rel_rules: tuple = ()
    rel_entities: tuple = ()
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
        import json as _json

        with contextlib.suppress(ValueError):
            rel_rules = tuple(
                tuple(x) for x in _json.loads(rules_json or "[]"))
            rel_entities = tuple(_json.loads(entities_json or "[]"))
        if not (rel_rules or rel_entities):
            logger.warning(
                "quality.relevance llm_only WITHOUT masking config — raw "
                "text goes to the LLM provider; pass rules_json/entities_json")
        work = llm_only_relevance(
            lake=lake, dataset=dataset, rows=sample,
            text_column=text_column, provider=provider,
            generalize_rules=rel_rules, entity_names=rel_entities,
        )
    else:
        from arrow_lake.annotation.dispatch import LSClient

        import json as _rules_json

        with contextlib.suppress(ValueError):
            rel_rules = tuple(
                tuple(x) for x in _rules_json.loads(rules_json or "[]"))
            rel_entities = tuple(_rules_json.loads(entities_json or "[]"))
        work = dispatch_relevance(
            ls_client=LSClient(aconf.ls_url, aconf.ls_api_token),
            project_title=name, rows=sample, text_column=text_column,
            provider=provider, ls_project_id=rec.get("ls_project_id"),
            bind_ls_project=lambda pid: astore.set_ls_project_id(name, pid),
            generalize_rules=rel_rules, entity_names=rel_entities,
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


# --------------------------------------------------------------------------- #
# W4.3 / F5.8 — 飞轮回流(decisions 低置信行 → 标注队列)
# --------------------------------------------------------------------------- #

class FeedbackRequest(BaseModel):
    object_rows: list[str] = Field(
        min_length=1, max_length=500,
        description="研判低置信/失败的行 id(stable_row_id)",
    )
    text_column: str = Field(default="text")
    project: str | None = Field(
        default=None, description="指定 L4 项目名(缺省自动找首个 active)",
    )
    generalize_rules: list[tuple[str, str]] = Field(
        default_factory=list,
        description="L2 泛化规则(security review 2026-08-30:空=透传,如实回报)",
    )
    entity_names: list[str] = Field(default_factory=list)


@router.post(
    "/feedback/{dataset}",
    status_code=200,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def feedback_loop(
    dataset: str,
    req: FeedbackRequest,
    request: Request,
    lake=Depends(get_lake),
    user=Depends(require_role(Role.ADMIN)),
) -> dict:
    """飞轮回流(F5.8):低置信行打回 L4 标注队列(strategy=feedback)。

    模型在哪错,标注就补哪:操作侧从研判结果挑出低置信行 → 幂等入队
    (LS 已有该行的 feedback task 即跳过)→ 人工重标 → 下一轮 assess
    反映。审计 ``quality.feedback``。
    """
    astore = getattr(request.app.state, "annotation_project_store", None)
    if astore is None:
        raise HTTPException(
            status_code=503, detail="system_db disabled; annotation registry unavailable",
        )
    cfg = getattr(request.app.state, "config", None)
    aconf = getattr(cfg, "annotation", None) if cfg is not None else None
    if not (getattr(aconf, "ls_url", None) and getattr(aconf, "ls_api_token", None)):
        raise HTTPException(
            status_code=503, detail="Label Studio not configured for feedback",
        )

    # 找 L4 项目(active+bound,非 relevance 反馈专线)
    if req.project is not None:
        proj = astore.get_project(req.project)
        if proj is None or proj.get("dataset") != dataset:
            raise HTTPException(404, detail=f"no project '{req.project}' for '{dataset}'")
    else:
        proj = next(
            (p for p in astore.list_projects()
             if p.get("dataset") == dataset and p.get("status") == "active"
             and p.get("ls_project_id")
             and not p["name"].endswith("__relevance")),
            None,
        )
    if proj is None:
        raise HTTPException(
            status_code=422,
            detail=f"no active annotation project for '{dataset}' "
                   "(relevance-only or unbound) — create an L4 project first",
        )
    ls_project_id = int(proj["ls_project_id"])

    try:
        source = lake.read_dataset(dataset)
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail=f"Dataset '{dataset}' not readable: {exc}",
        ) from exc
    from arrow_lake.annotation.dispatch import LSClient, stable_row_id
    from arrow_lake.annotation.masking import apply_annotation_masking

    text_of: dict[str, str] = {}
    for i, row in enumerate(source.to_pylist()):
        text = str(row.get(req.text_column) or "").strip()
        if text:
            text_of[stable_row_id(text, i)] = text
    missing = [r for r in req.object_rows if r not in text_of]
    wanted = [r for r in req.object_rows if r in text_of]

    # 测试/运维注入缝(relevance_provider 同款先例);缺省真 LSClient
    factory = getattr(
        request.app.state, "annotation_ls_client_factory", None) or LSClient
    client = factory(aconf.ls_url, aconf.ls_api_token)
    # 幂等:已有 feedback task(任意状态)的行跳过
    queued: set[str] = set()
    with contextlib.suppress(Exception):
        for t in client.export_tasks(ls_project_id):
            data = t.get("data") or {}
            if str(data.get("strategy")) == "feedback":
                queued.add(str(data.get("row_id") or ""))
    fresh = [r for r in wanted if r not in queued]
    fb_rules = tuple(tuple(x) for x in req.generalize_rules)
    fb_entities = tuple(req.entity_names)
    if not (fb_rules or fb_entities):
        logger.warning(
            "quality.feedback dispatched WITHOUT masking config (passthrough) "
            "dataset=%s rows=%d — supply generalize_rules/entity_names",
            dataset, len(fresh))
    tasks = [{
        "data": {
            "text": apply_annotation_masking(
                text_of[r], generalize_rules=fb_rules,
                entity_names=fb_entities, hmac_key=None),
            "row_id": r, "strategy": "feedback",
        },
    } for r in fresh]
    if tasks:
        client.import_tasks(ls_project_id, tasks)

    actor = getattr(user, "sub", "system")
    with contextlib.suppress(Exception):
        lake.audit_record(
            "quality.feedback", dataset_name=dataset, actor=actor,
            payload={"project": proj["name"], "requested": len(req.object_rows),
                     "queued": len(fresh), "skipped": len(queued & set(wanted)),
                     "missing_rows": missing[:20]},
        )
    return {
        "dataset": dataset, "project": proj["name"],
        "requested": len(req.object_rows), "queued": len(fresh),
        "already_queued": len(queued & set(wanted)),
        "missing_rows": missing,
        "masking": {"applied": bool(fb_rules or fb_entities)},
    }


@router.get(
    "/relevance/{dataset}/refeed",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def relevance_refeed(
    dataset: str,
    request: Request,
    lake=Depends(get_lake),
    _user=Depends(require_role(Role.ADMIN)),
) -> dict:
    """F5.2 反哺清单(设计 §3.3,收官审计 2026-08-31 补齐)。

    相关性评估结果的行级反哺:判「不相关」的行 → **过滤整改建议**
    (从数据集清出的候选清单);判「高相关」的行 → **补标优先清单**
    (经 ``POST /quality/feedback`` 打回 L4 队列,F5.8 同路)。只读派生,
    不落库——每次按当前 ADL 实时计算。
    """
    _contract_store(request)  # 仅作 system_db 在线性检查
    try:
        lake.read_dataset(dataset)
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail=f"Dataset '{dataset}' not readable: {exc}",
        ) from exc
    adl = _read_table(lake, f"{dataset}_adl", table=None)
    if adl is not None and adl.num_rows == 0:
        adl = None
    res = compute_relevance(adl)
    if res.score is None:
        return {
            "dataset": dataset, "relevance_score": None,
            "note": res.details.get("note", "no relevance annotations"),
            "filter_suggestions": [], "priority_annotate": [],
        }
    d = res.details
    return {
        "dataset": dataset,
        "relevance_score": res.score,
        "source": res.source,
        "counts": d["counts"],
        "filter_suggestions": d["irrelevant_row_ids"],
        "priority_annotate": d["high_relevance_row_ids"],
        "usage": {
            "filter": "irrelevant 行建议从训练集过滤(整改建议,人工确认后执行)",
            "annotate": "high 行经 POST /quality/feedback 优先补 L4 标注",
        },
    }
