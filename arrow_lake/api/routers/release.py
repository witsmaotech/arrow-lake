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
from pathlib import Path
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


def _read_source(
    lake: Any, dataset: str, table: str | None, *, version: int | None = None,
    columns: list[str] | None = None,
) -> Any:
    try:
        source = lake.read_dataset(
            dataset, table=table, version=version, columns=columns)
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
    """发布:校验链 → 锁版本+tag+规格书 → 基线快照 → 审计/血缘。

    H15(四维 review):同步链(版本锁定/读表/漂移/准入/datasheet/落库/
    基线快照/audit)整体收 ``run_sync(olap_executor)``——此前跑在 event
    loop 上,大表漂移扫描分钟级独占该 worker。M9:锁版本**先行**,之后
    全链按该 version 读——datasheet 与 drift 基线快照消费同一 Lance
    版本(原 source 先读/版本后锁/基线用旧 source 的三读时点漂移)。
    """
    store = _store(request, "release_store", "release registry")
    qstore = _store(request, "quality_report_store", "quality reports")
    dstore = _store(request, "drift_baseline_store", "drift baselines")
    cstore = _store(request, "contract_store", "contract registry")
    actor = getattr(user, "sub", "system")

    def _work() -> dict[str, Any]:
        # M9:版本锁定先行 → 后续 read/drift/datasheet/基线全按此版本
        try:
            lance_version = int(
                lake._get_storage().open_dataset(dataset, table=table).version)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"cannot lock Lance version for '{dataset}': {exc}",
            ) from exc
        source = _read_source(lake, dataset, table, version=lance_version)

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

        adl = None
        with contextlib.suppress(Exception):
            adl = lake.read_dataset(f"{dataset}_adl")

        datasheet = build_datasheet(
            dataset=dataset, tag=tag, lance_version=lance_version,
            report=report,
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

        # 发布时自动快照基线(设计 §5);best-effort 不阻塞发布;
        # source 按 lance_version 读 → 基线与锁定的版本严格同源(M9)
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
        return {"rec": rec, "datasheet": datasheet, "forced": forced,
                "reasons": reasons, "tag": tag,
                "lance_version": lance_version,
                "total_score": report.get("total_score")}

    from arrow_lake.api.utils import olap_executor, run_sync

    out = await run_sync(_work, timeout=900.0, label="release_create",
                         executor=olap_executor)
    with contextlib.suppress(Exception):
        await run_sync(
            lake.lineage_record_event, dataset, "release.published",
            actor=actor,
            metadata={
                "tag": out["tag"], "lance_version": out["lance_version"],
                "total_score": out["total_score"],
            },
            timeout=5.0, label="release_lineage",
        )

    return {
        **out["rec"], "datasheet": out["datasheet"],
        "forced": out["forced"],
        "overridden_reasons": out["reasons"] if out["forced"] else [],
    }


@router.get(
    "/{dataset}",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def release_history(dataset: str, request: Request) -> dict:
    """发布历史(newest-first)+ 最新 active。"""
    store = _store(request, "release_store", "release registry")
    releases = store.list_releases(dataset)
    latest_active = store.latest_release(dataset)  # M17:一次查询复用
    return {"dataset": dataset, "total": len(releases), "releases": [
        {k: v for k, v in r.items() if k != "datasheet_yaml"}
        for r in releases
    ], "latest_active": (
        {k: v for k, v in latest_active.items() if k != "datasheet_yaml"}
        if latest_active else None)}


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


# --------------------------------------------------------------------------- #
# W4.1/F5.6 — 语料四形态导出
# --------------------------------------------------------------------------- #

class CorpusRequest(BaseModel):
    generalize_rules: list[tuple[str, str]] = Field(
        default_factory=list,
        description="L2 泛化规则 [(regex, replacement)](脱敏=配置驱动)",
    )
    entity_names: list[str] = Field(
        default_factory=list, description="L3 假名实体名(HMAC 需已配)",
    )


def _sft_system_prompt(dataset: str, contract: Any, request: Request) -> str:
    """system = 本体对象类 + active 规则(设计 §8①;best-effort)。"""
    lines = [f"你是数据集 {dataset} 的领域标注专家。"]
    if contract is not None:
        classes = [s.object_class for s in contract.tables.values()
                   if s.object_class]
        if classes:
            lines.append("本体对象类:" + "、".join(sorted(set(classes))))
    rules_store = getattr(request.app.state, "ontology_rules_store", None)
    if rules_store is not None and hasattr(rules_store, "list_rules"):
        with contextlib.suppress(Exception):
            # 字段口径:ontology_rules 表 = rule_id/condition_expr/conclusion
            active = [
                r for r in rules_store.list_rules()
                if r.get("status") == "active"
            ]
            if active:
                lines.append("必须遵守的 active 规则:")
                lines.extend(
                    f"- {r['rule_id']}: 当 {r.get('condition_expr') or '?'} "
                    f"则 {r.get('conclusion') or '?'}"
                    for r in active[:20] if r.get("rule_id"))
    lines.append("按五段结构(对象/事件/适用规则/场景/关系)输出标注。")
    return "\n".join(lines)




def _build_rlhf_pairs(
    *, request: Request, dataset: str, rows: list[dict[str, Any]],
    adl: Any, text_column: str,
    rules: tuple, entities: tuple,
) -> tuple[list[dict[str, Any]], str | None]:
    """RLHF 偏好对(decisions_history × ADL,设计 §8③)。

    配对键:契约 identifier 列的值(=decisions 的 object_id)→ 源行文本
    → stable_row_id → ADL 最新人工 L4(chosen);rejected=该对象的最新
    模型研判结论。任一侧缺失的对象跳过;无历史 → 空导出+提示。
    """
    from arrow_lake.release.corpus import _latest_human_by_row, build_rlhf_records

    hstore = getattr(request.app.state, "decisions_history_store", None)
    cstore = _store(request, "contract_store", "contract registry")
    contract, _ = _load_contract_and_spec(cstore, dataset)
    if hstore is None or contract is None:
        return [], ("decisions history or contract unavailable — RLHF "
                    "pairing needs both; empty export")
    history = hstore.list_history(dataset, limit=500)
    if not history:
        return [], ("no recorded decisions — POST /decisions/assess?"
                    "record_history=true to build the RLHF source")

    # 配对:object_id 按值在源行全列扫描(契约 identifier 值即 object_id;
    # 不要求契约显式声明 identifier——值匹配对 demo/无 identifier 契约同样成立)。
    # review 修:①倒排一次建 value→text(此前 O(H×R×C) 逐值 str() 十亿量级);
    # ②text 与 corpus rows 同口径 strip(此前带首尾空白即 hash 不配对,L4)
    pairs: list[dict[str, Any]] = []
    by_row = _latest_human_by_row(adl) if adl is not None else {}
    from arrow_lake.annotation.dispatch import stable_row_id

    value_to_text: dict[str, str] = {}
    for r in rows:
        text = str(r.get(text_column) or "").strip()
        if not text:
            continue
        for v in r.values():
            if v is None:
                continue
            s = str(v)
            if s and s not in value_to_text:
                value_to_text[s] = text
    for h in history:
        oid = str(h.get("object_id") or "")
        if not oid:
            continue
        target_text = value_to_text.get(oid)
        if not target_text:
            continue
        rid = stable_row_id(target_text, 0)
        expert = by_row.get(rid)
        if expert is None:
            continue
        rejected = {
            "rule_ids": h.get("rule_ids") or [],
            "conclusions": h.get("conclusions") or [],
            "matched_rules": h.get("matched_rules"),
        }
        pairs.append({
            "prompt": target_text,
            "chosen": {
                "objects": expert.get("objects") or [],
                "events": expert.get("events") or [],
                "rules_applied": expert.get("rules_applied") or [],
                "scenario": expert.get("scenario") or "",
                "relations": expert.get("relations") or [],
            },
            "rejected": rejected,
        })
    records = build_rlhf_records(
        pairs=pairs, generalize_rules=rules, entity_names=entities)
    note = None if records else "no expert/model pairs resolvable (need ADL L4 + recorded decisions on the same objects)"
    return records, note



@router.post(
    "/{dataset}/corpus",
    status_code=200,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def export_corpus(
    dataset: str,
    req: CorpusRequest,
    request: Request,
    form: str = Query(description="sft | pretrain | rlhf | golden"),
    text_column: str = Query(default="text"),
    table: str | None = Query(default=None, description="容器内表名"),
    allow_unmasked: bool = Query(
        default=False,
        description="红线④ fail-closed 的显式豁免(audit corpus.unmasked)",
    ),
    lake=Depends(get_lake),
    user=Depends(require_role(Role.ADMIN)),
) -> dict:
    """语料导出(F5.6):四形态之一 → ``{export_dir}/{tag}/{form}.jsonl``。

    全部经 masking 出域(规则随请求注入,脱敏=配置驱动);须存在
    **active 发布**(导出目录按其 tag);③RLHF 在 decisions 持久化前为
    空导出+提示(设计风险表口径)。
    """
    from arrow_lake.release.corpus import (
        build_golden_records,
        build_pretrain_records,
        build_sft_records,
        write_corpus,
    )

    if form not in ("sft", "pretrain", "rlhf", "golden"):
        raise HTTPException(
            status_code=422, detail=f"unknown corpus form '{form}'",
        )
    store = _store(request, "release_store", "release registry")
    rel = store.latest_release(dataset)
    if rel is None:
        raise HTTPException(
            status_code=422,
            detail=f"no active release for '{dataset}' — publish first",
        )

    from arrow_lake.annotation.dispatch import stable_row_id

    # H11(四维 review):按形态列裁剪——sft/golden 只需 text 列;rlhf 按
    # object_id 值反查需全列;pretrain 走 KG 快照不用源行。此前四形态
    # 一律全表全列物化(500k 行 × 30 列 = 数 GB)。i 与 texts 对齐
    # (stable_row_id 语义)不受列裁剪影响。
    rows: list[dict[str, Any]] = []
    if form != "pretrain":
        try:
            source = _read_source(
                lake, dataset, table,
                columns=None if form == "rlhf" else [text_column])
        except Exception:
            source = _read_source(lake, dataset, table)  # text 列缺 → 宽容全列
        for i, raw in enumerate(source.to_pylist()):
            t = str(raw.get(text_column) or "").strip()
            if not t:
                continue
            rows.append({"row_id": stable_row_id(t, i), **{
                k: v for k, v in raw.items() if v is not None}})
    adl = None
    with contextlib.suppress(Exception):
        adl = lake.read_dataset(f"{dataset}_adl")

    rules = tuple(tuple(x) for x in req.generalize_rules)
    entities = tuple(req.entity_names)
    note: str | None = None

    # 红线④ fail-closed(security review 2026-08-30):语料出域必须带
    # 脱敏配置;无配置即拒(豁免须显式,audit corpus.unmasked 留痕)。
    # M1(四维 review):pretrain 纳入门禁——definition 是源文本抽取片段
    # 可含 PII,此前被 text_bearing 排除在门外原文出域,与 datasheet
    # 「all forms masked」宣称矛盾。
    actor = getattr(user, "sub", "system")
    masked = bool(rules or entities)
    if not masked:
        if not allow_unmasked:
            raise HTTPException(
                status_code=422,
                detail="corpus export requires masking config "
                       "(generalize_rules / entity_names) — red line ④; "
                       "explicit ?allow_unmasked=true overrides (audited)",
            )
        if allow_unmasked:
            logger.warning(
                "corpus.exported UNMASKED dataset=%s form=%s actor=%s "
                "(allow_unmasked override)", dataset, form, actor)
            with contextlib.suppress(Exception):
                lake.audit_record(
                    "corpus.unmasked", dataset_name=dataset, actor=actor,
                    payload={"form": form, "tag": rel["tag"]},
                )

    if form == "sft":
        cstore = _store(request, "contract_store", "contract registry")
        contract, _ = _load_contract_and_spec(cstore, dataset)
        records = build_sft_records(
            rows=rows, adl=adl, text_column=text_column,
            system_prompt=_sft_system_prompt(dataset, contract, request),
            generalize_rules=rules, entity_names=entities,
        )
    elif form == "pretrain":
        client = None
        with contextlib.suppress(Exception):
            client = lake._get_kg_client()
        if client is None:
            records, note = [], "KG disabled or unreachable — empty export"
        else:
            graph = f"kg_{dataset}"
            # 预置空:快照失败(kg_{ds} 未建图/超时)时 suppress 吞异常,
            # 若不预置则 with 块外 UnboundLocalError → 500(四维 review H3)
            vertices, edges = [], []
            with contextlib.suppress(Exception):
                vertices, edges = await client.get_graph_snapshot(
                    graph_name=graph, limit=1000)
            # M1:definition 段应用与 SFT 同款 L2/L3 规则(带配置时)
            def _mask_def(s: str) -> str:
                from arrow_lake.annotation.masking import apply_annotation_masking

                return apply_annotation_masking(
                    s, generalize_rules=rules, entity_names=entities,
                    hmac_key=None)
            records = build_pretrain_records(
                vertices=vertices, edges=edges,
                definition_masker=_mask_def if masked else None)
            if not records:
                note = f"no resolvable triples in KG '{graph}'"
    elif form == "rlhf":
        # 发版前清偿 D 项:decisions_history×ADL 自动配对(chosen=专家 L4,
        # rejected=模型研判);无历史/无可配对仍空导出+提示(不阻塞)
        records, note = _build_rlhf_pairs(
            request=request, dataset=dataset, rows=rows, adl=adl,
            text_column=text_column, rules=rules, entities=entities,
        )
    else:  # golden
        records = build_golden_records(
            rows=rows, adl=adl, text_column=text_column,
            generalize_rules=rules, entity_names=entities,
        )

    cfg = getattr(request.app.state, "config", None)
    base = getattr(getattr(cfg, "export", None), "base_dir", None) \
        or "/data/lake/exports"
    path = write_corpus(
        Path(base), tag=rel["tag"], form=form, records=records)

    with contextlib.suppress(Exception):
        lake.audit_record(
            "corpus.exported", dataset_name=dataset, actor=actor,
            payload={"form": form, "tag": rel["tag"], "records": len(records),
                     "path": str(path)},
        )
    from arrow_lake.api.utils import run_sync

    with contextlib.suppress(Exception):
        await run_sync(
            lake.lineage_record_event, dataset, "corpus.exported",
            actor=actor,
            metadata={"form": form, "tag": rel["tag"],
                      "records": len(records)},
            timeout=5.0, label="corpus_lineage",
        )
    return {
        "dataset": dataset, "tag": rel["tag"], "form": form,
        "records": len(records), "path": str(path),
        "masking": {"applied": masked, "rules": len(rules),
                      "entities": len(entities)}, "note": note,
    }
