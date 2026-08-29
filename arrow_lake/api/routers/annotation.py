"""标注项目注册 CRUD(v1.11.3 MS4 W1.4,F4.2)。

旁路面:全部 ADMIN;沿 actions 路由约定(system_db 关闭 → 503)。
项目 = AL 侧注册(名称/源数据集/绑定模板 + 生成的 LS label_config);
**不在创建时调 LS**——LS 是 transient 工作区,W2 dispatch 时才懒创建
LS project 并回填 ls_project_id(SoT 在本表,设计 §0 红线)。

labeling_config 两条路(S2):默认从绑定模板经 template_gen 生成;
``labeling_config_override`` 良构即透传(手写高级覆盖)。
"""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from arrow_lake.annotation.masking import AnnotationMaskingError
from arrow_lake.annotation.sampler import SampleBudget
from arrow_lake.annotation.template_gen import TemplateGenError, generate_ls_config
from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import get_lake, require_role

router = APIRouter(prefix="/api/v1/annotation", tags=["annotation"])


def _store(request: Request) -> Any:
    store = getattr(request.app.state, "annotation_project_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; annotation registry unavailable")
    return store


def _template_yaml(name: str) -> str | None:
    """模板 gallery 按名取 YAML 文本(找不到 → None → 422)。"""
    from arrow_lake.knowledge_graph.doc_type_router import get_template_gallery

    for t in get_template_gallery().templates:
        if t.name == name.lower():
            with open(t.path, encoding="utf-8") as fh:
                return fh.read()
    return None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    dataset: str = Field(min_length=1, max_length=200)
    template_name: str = Field(min_length=1, max_length=128)
    labeling_config_override: str | None = Field(
        default=None, max_length=64_000,
        description="手写 LS XML 高级覆盖(S2);缺省从绑定模板生成",
    )


@router.get("/projects", dependencies=[Depends(require_role(Role.ADMIN))])
async def list_projects(request: Request) -> dict:
    projects = _store(request).list_projects()
    return {"total": len(projects), "projects": projects}


@router.get("/projects/{name}", dependencies=[Depends(require_role(Role.ADMIN))])
async def get_project(name: str, request: Request) -> dict:
    rec = _store(request).get_project(name)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No annotation project '{name}'")
    return rec


@router.post(
    "/projects",
    status_code=200,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def create_project(req: ProjectCreate, request: Request) -> dict:
    store = _store(request)
    manual: str | None = None
    template_yaml: str = ""
    if req.labeling_config_override is not None:
        manual, config_source = req.labeling_config_override, "manual"
    else:
        template_yaml = _template_yaml(req.template_name) or ""
        if not template_yaml:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown template '{req.template_name}' (not in gallery)",
            )
        config_source = "generated"
    try:
        cfg = generate_ls_config(template_yaml, manual_config=manual)
    except TemplateGenError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid labeling config: {exc}") from exc
    rec = store.create_project(
        name=req.name,
        dataset=req.dataset,
        template_name=req.template_name,
        labeling_config=cfg.xml,
        config_source=config_source,
    )
    if rec is None:
        raise HTTPException(status_code=422, detail=f"Annotation project '{req.name}' already exists")
    return rec


@router.delete("/projects/{name}", dependencies=[Depends(require_role(Role.ADMIN))])
async def delete_project(name: str, request: Request) -> dict:
    if not _store(request).delete_project(name):
        raise HTTPException(status_code=404, detail=f"No annotation project '{name}'")
    return {"name": name, "deleted": True}


# --------------------------------------------------------------------------- #
# dispatch(W2.4):采样 → 脱敏 → 预标注 → LS import 一键(202 后台)         #
# --------------------------------------------------------------------------- #


class DispatchRequest(BaseModel):
    project: str = Field(min_length=1, max_length=128)
    total: int | None = Field(default=None, ge=1, le=1000)
    text_column: str = Field(default="text", min_length=1, max_length=200)
    budget: dict[str, float] | None = Field(
        default=None,
        description='策略权重覆盖,如 {"uncertainty": 0.6, "diversity": 0.4}',
    )
    generalize_rules: list[list[str]] | None = Field(
        default=None,
        description="L2 泛化 [[regex, replacement], ...](S7;dispatch 前应用)",
    )
    entity_names: list[str] | None = Field(
        default=None, description="L3 假名实体名列表(HMAC,密钥复用 masking 配置)",
    )


def _load_rows(lake: Any, dataset: str, text_column: str, cap: int) -> tuple[list[dict], list[str] | None]:
    """主表 + 死信表(若有)拼候选池;返回 (rows, dead_row_ids)。

    死信行是被质量门拒掉的行——不在主表,但正是 failure_case 策略的
    目标样本,拼进候选池尾段(只取 text_column + quality_score)。
    """
    def _to_dicts(table: Any, wanted: list[str]) -> list[dict]:
        cols = [c for c in wanted if c in table.column_names]
        if text_column in table.column_names and text_column not in cols:
            cols.append(text_column)
        if text_column not in cols:
            return []  # 文本列缺失 → 该表不构成候选
        return table.select(cols).to_pylist()

    rows = _to_dicts(lake.read_dataset(dataset), ["quality_score", text_column])[:cap]
    dead_ids: list[str] | None = None
    try:
        dead = _to_dicts(lake.read_dataset(f"_{dataset}_dead_letter"), [text_column])
        if dead:
            dead = dead[:cap]
            dead_ids = [f"r{len(rows) + i}" for i in range(len(dead))]
            rows.extend(dead)
    except Exception:  # 死信表不存在 = 无 failure_case 源
        pass
    return rows, dead_ids


def _bg_dispatch(
    app_state: Any, lake: Any, actor: str, project: str, rows: list[dict],
    *, text_column: str, total: int, budget: SampleBudget,
    quality_scores: dict[str, float] | None, dead_row_ids: list[str] | None,
    generalize_rules: list[tuple[str, str]], entity_names: list[str],
    ls_url: str, ls_token: str, import_batch_size: int,
    ls_opener: Any = None,
) -> dict:
    """后台 worker:run_dispatch 全链 + audit(成功与失败都记)。

    ``ls_opener`` 仅测试注入(真调用默认 urllib urlopen)。
    """
    import asyncio

    from arrow_lake.annotation.dispatch import LSClient, LSClientError, run_dispatch

    store = app_state.annotation_project_store
    rec = store.get_project(project)
    extractor_factory = getattr(lake, "_get_kg_extractor", None)
    extractor = extractor_factory() if extractor_factory else None

    def _audit(event: str, payload: dict) -> None:
        # audit best-effort,不阻塞派发结果
        with contextlib.suppress(Exception):
            lake.audit_record(event, dataset_name=rec["dataset"], actor=actor, payload=payload)

    try:
        outcome = asyncio.run(run_dispatch(
            project=project, dataset=rec["dataset"],
            labeling_config=rec["labeling_config"],
            ls_project_id=rec.get("ls_project_id"),
            rows=rows, text_column=text_column, total=total, budget=budget,
            quality_scores=quality_scores, embeddings=None,
            dead_row_ids=dead_row_ids, committee=None,
            generalize_rules=generalize_rules, entity_names=entity_names,
            hmac_key=None, ls_client=LSClient(ls_url, ls_token, opener=ls_opener),
            extractor=extractor, bind_ls_project=store.set_ls_project_id,
            import_batch_size=import_batch_size,
        ))
    except (LSClientError, AnnotationMaskingError) as exc:
        _audit("annotation.dispatch", {"project": project, "status": "failed", "error": str(exc)})
        raise
    _audit("annotation.dispatch", {
        "project": project, "status": "ok", "ls_project_id": outcome.ls_project_id,
        "dispatched": outcome.dispatched, "skipped": outcome.skipped,
        "strategies": outcome.strategies,
    })
    return {
        "project": project, "ls_project_id": outcome.ls_project_id,
        "dispatched": outcome.dispatched, "skipped": outcome.skipped,
        "strategies": outcome.strategies,
    }


@router.post(
    "/dispatch",
    status_code=202,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def dispatch(
    req: DispatchRequest,
    request: Request,
    lake=Depends(get_lake),
    user=Depends(require_role(Role.ADMIN)),
) -> dict:
    """一键派发:读项目注册表 → 组装候选池(主表+死信)→ 后台全链。"""
    from arrow_lake.api.tasks import TaskManager, spawn_background

    store = _store(request)
    rec = store.get_project(req.project)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No annotation project '{req.project}'")
    if rec.get("status") != "active":
        raise HTTPException(status_code=422, detail=f"Project '{req.project}' is closed")

    config = request.app.state.config.annotation
    if not config.ls_url or not config.ls_api_token:
        raise HTTPException(
            status_code=503,
            detail="Label Studio not configured (annotation.ls_url / annotation.ls_api_token)",
        )
    total = req.total or config.default_sample_total
    if total > config.max_sample_total:
        raise HTTPException(
            status_code=422,
            detail=f"total {total} exceeds max_sample_total {config.max_sample_total}",
        )

    rows, dead_ids = _load_rows(lake, rec["dataset"], req.text_column, config.candidate_pool_cap)
    if not rows:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No rows readable from '{rec['dataset']}' "
                f"(missing column '{req.text_column}'?)"
            ),
        )
    quality_scores = None
    if rows and "quality_score" in rows[0]:
        quality_scores = {
            f"r{i}": float(r["quality_score"])
            for i, r in enumerate(rows) if r.get("quality_score") is not None
        }
    budget = SampleBudget(**(req.budget or {}))
    rules = [(p, r) for p, r in (req.generalize_rules or [])]

    task_id = TaskManager.create_task("annotation_dispatch", req.project, user_id=user.user_id)
    spawn_background(TaskManager.run_background(
        task_id, _bg_dispatch,
        request.app.state, lake, user.username, req.project, rows,
        text_column=req.text_column, total=total, budget=budget,
        quality_scores=quality_scores, dead_row_ids=dead_ids,
        generalize_rules=rules, entity_names=req.entity_names or [],
        ls_url=config.ls_url, ls_token=config.ls_api_token,
        import_batch_size=config.import_batch_size,
    ))
    return {
        "task_id": task_id, "operation": "annotation_dispatch",
        "project": req.project, "candidate_rows": len(rows),
        "message": "annotation dispatch started (poll /api/v1/tasks/{task_id}/status)",
    }


# --------------------------------------------------------------------------- #
# recover(W3.4):手动同步 LS → 解析 → 仲裁 → ADL 写回                         #
# --------------------------------------------------------------------------- #


class RecoverRequest(BaseModel):
    project: str = Field(min_length=1, max_length=128)


def _ls_tasks_payload(raw: Any) -> list[dict]:
    """LS GET /api/tasks 响应 → task 列表(兼容 {"tasks":[...]} 与裸 list)。"""
    if isinstance(raw, dict):
        tasks = raw.get("tasks")
        return tasks if isinstance(tasks, list) else []
    return raw if isinstance(raw, list) else []


def _existing_adl_state(
    lake: Any, dataset: str
) -> tuple[set[str], dict[tuple[str, str], int]]:
    """读现有 {ds}_adl → (adl_id 集, (row,annotator)→最大版本)。表无 → 空。"""
    try:
        table = lake.read_dataset(f"{dataset}_adl")
    except Exception:  # 表不存在 = 首次回收
        return set(), {}
    ids = set()
    versions: dict[tuple[str, str], int] = {}
    for row in table.select(
        ["adl_id", "source_row_id", "annotator_id", "adl_version"]
    ).to_pylist():
        ids.add(row["adl_id"])
        key = (row["source_row_id"], row["annotator_id"])
        versions[key] = max(versions.get(key, 0), int(row["adl_version"] or 0))
    return ids, versions


@router.post(
    "/recover",
    status_code=200,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def recover(
    req: RecoverRequest,
    request: Request,
    lake=Depends(get_lake),
    user=Depends(require_role(Role.ADMIN)),
) -> dict:
    """手动同步(轮询对账的同一代码路径,S9):LS 增量 → 五段解析 →
    仲裁(kappa/分歧)→ ADL 版本化写回(adl_id 幂等)→ audit。"""
    import uuid as _uuid

    from arrow_lake._lake_ingest import _DeadLetterStorageAdapter
    from arrow_lake.annotation.adl import build_adl_batch, write_adl
    from arrow_lake.annotation.dispatch import LSClient
    from arrow_lake.annotation.quality import adjudicate, project_kappa
    from arrow_lake.annotation.recover import incremental_tasks, parse_ls_annotation

    store = _store(request)
    rec = store.get_project(req.project)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No annotation project '{req.project}'")
    config = request.app.state.config.annotation
    if not config.ls_url or not config.ls_api_token:
        raise HTTPException(
            status_code=503,
            detail="Label Studio not configured (annotation.ls_url / annotation.ls_api_token)",
        )
    ls_project_id = rec.get("ls_project_id")
    if not ls_project_id:
        raise HTTPException(
            status_code=422,
            detail=f"Project '{req.project}' has no LS binding yet — dispatch first",
        )

    client = LSClient(config.ls_url, config.ls_api_token)
    tasks = _ls_tasks_payload(client.list_tasks(ls_project_id, page_size=200))
    watermark = int(rec.get("recover_watermark") or 0)
    fresh, new_watermark = incremental_tasks(tasks, watermark=watermark)

    recovered = [a for t in fresh for a in parse_ls_annotation(t)]
    by_task: dict[str, list] = {}
    for ann in recovered:
        by_task.setdefault(ann.row_id, []).append(ann)
    verdicts = adjudicate(by_task)

    existing_ids, group_versions = _existing_adl_state(lake, rec["dataset"])
    batch_id = f"rec-{_uuid.uuid4().hex[:8]}"
    table, written = build_adl_batch(
        dataset=rec["dataset"], recovered=recovered, adjudications=verdicts,
        batch_id=batch_id, existing_adl_ids=existing_ids,
        group_versions=group_versions,
    )
    if written:
        write_adl(_DeadLetterStorageAdapter(lake._get_storage()), rec["dataset"], table)
    store.set_watermark(req.project, new_watermark)

    kappa = project_kappa(by_task)
    counts = {"approved": 0, "arbitration": 0, "pending": 0}
    for verdict in verdicts.values():
        counts[verdict.status] = counts.get(verdict.status, 0) + 1
    summary = {
        "project": req.project,
        "tasks_seen": len(fresh),
        "annotations_recovered": len(recovered),
        "adl_rows_written": written,
        "review": counts,
        "kappa": kappa,
        "watermark": new_watermark,
        "batch_id": batch_id,
    }
    with contextlib.suppress(Exception):  # audit best-effort
        lake.audit_record(
            "annotation.recover",
            dataset_name=rec["dataset"],
            actor=user.username or user.sub,
            payload=summary,
        )
    return summary


@router.get(
    "/projects/{name}/adl",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def get_adl(name: str, request: Request, lake=Depends(get_lake)) -> dict:
    """ADL 可见性:最近写回行(默认前 50,倒序)。"""
    store = _store(request)
    rec = store.get_project(name)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No annotation project '{name}'")
    dataset = rec["dataset"]
    try:
        table = lake.read_dataset(f"{dataset}_adl")
    except Exception as exc:  # 未 dispatch/recover 过
        raise HTTPException(
            status_code=404, detail=f"No ADL table for '{dataset}' yet (recover first): {exc}"
        ) from exc
    rows = table.to_pylist()
    rows.reverse()  # 最近写回在前
    return {"project": name, "dataset": dataset, "total": len(rows), "rows": rows[:50]}


@router.post("/webhook")
async def ls_webhook(request: Request, lake=Depends(get_lake)) -> dict:
    """LS webhook 接收(S9 加速通道;轮询对账为主)。

    无 AL 认证——LS 是内网旁路容器,D1 凭据方向是 AL→LS;本端点只做
    解析 + 审计,不写 ADL(数据回收统一走 recover,adl_id 幂等保证
    webhook/轮询双通道不重复)。
    """
    from arrow_lake.annotation.recover import parse_webhook

    try:
        payload = await request.json()
    except Exception:  # 非 JSON(探测/误投)
        return {"accepted": False}
    if not isinstance(payload, dict):
        return {"accepted": False}
    rec = parse_webhook(payload)
    action = str(payload.get("action") or "")
    if rec is None:
        return {"accepted": False, "action": action}
    with contextlib.suppress(Exception):
        lake.audit_record(
            "annotation.webhook",
            dataset_name="",
            actor=f"ls-user:{rec.annotator_id}",
            payload={
                "action": action, "task_id": rec.task_id, "row_id": rec.row_id,
                "annotator": rec.annotator_id,
            },
        )
    return {"accepted": True, "action": action, "row_id": rec.row_id}
