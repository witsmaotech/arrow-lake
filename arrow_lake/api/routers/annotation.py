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
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from arrow_lake.annotation.masking import AnnotationMaskingError
from arrow_lake.annotation.sampler import SampleBudget
from arrow_lake.annotation.template_gen import TemplateGenError, generate_ls_config
from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import audit_write, get_lake, require_role

router = APIRouter(prefix="/api/v1/annotation", tags=["annotation"])

logger = logging.getLogger(__name__)


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
    # ls_url(浏览器可达变体优先)外露给前端渲染「打开 LS」入口(M4);
    # getattr 防御:测试 fixture 等 lifespan 未跑的环境 state.config 可缺
    cfg = getattr(getattr(request.app.state, "config", None),
                  "annotation", None)
    return {"total": len(projects), "projects": projects,
            "ls_url": (cfg.ls_public_url or cfg.ls_url or None)
            if cfg is not None else None}


@router.get(
    "/projects/{name}/status",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def get_project_status(name: str, request: Request) -> dict:
    """项目当前看板(只读):review 统计 + Fleiss κ(全量 tasks)。

    与手动回收共用聚合逻辑但零副作用——后台 30s 自动回收的进度
    由此对 UI 可见(M3);不写 ADL、不生成仲裁、不动 watermark。
    """
    from arrow_lake.annotation.sync import project_status
    from arrow_lake.api.utils import ls_io_executor, run_sync

    config = request.app.state.config.annotation
    if not (config.ls_url and config.ls_api_token):
        raise HTTPException(
            status_code=503, detail="Label Studio not configured",
        )
    try:
        return await run_sync(
            lambda: project_status(
                store=_store(request), config=config, project_name=name),
            timeout=120.0, label="annotation_status",
            executor=ls_io_executor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # LS 不可达等(看板容错,不阻断其他面板)
        raise HTTPException(
            status_code=502, detail=f"LS status fetch failed: {exc}",
        ) from exc


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
async def create_project(req: ProjectCreate, request: Request,
                            user=Depends(require_role(Role.ADMIN))) -> dict:
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
    audit_write(request, "annotation.project_created",
                actor=user.username or user.sub, dataset=req.dataset,
                payload={"project": req.name, "template": req.template_name,
                         "config_source": config_source})
    return rec


@router.delete("/projects/{name}", dependencies=[Depends(require_role(Role.ADMIN))])
async def delete_project(name: str, request: Request,
                            user=Depends(require_role(Role.ADMIN))) -> dict:
    rec = _store(request).get_project(name)
    if not _store(request).delete_project(name):
        raise HTTPException(status_code=404, detail=f"No annotation project '{name}'")
    audit_write(request, "annotation.project_deleted",
                actor=user.username or user.sub,
                dataset=str(rec.get("dataset") or "") if rec else "",
                payload={"project": name,
                         "ls_project_id": rec.get("ls_project_id") if rec else None})
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
    review 修:先 ``slice(0, cap)`` 再 to_pylist——cap 是内存护栏,此前
    全表物化后才 ``[:cap]`` 形同虚设(107M 行级数据集 GB 级 churn)。
    """
    def _to_dicts(table: Any, wanted: list[str], limit: int) -> list[dict]:
        cols = [c for c in wanted if c in table.column_names]
        if text_column in table.column_names and text_column not in cols:
            cols.append(text_column)
        if text_column not in cols:
            return []  # 文本列缺失 → 该表不构成候选
        return table.select(cols).slice(0, limit).to_pylist()

    try:
        main = lake.read_dataset(dataset)
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail=f"Dataset '{dataset}' not readable: {exc}",
        ) from exc
    rows = _to_dicts(main, ["quality_score", text_column], cap)
    dead_ids: list[str] | None = None
    try:
        dead = _to_dicts(lake.read_dataset(f"_{dataset}_dead_letter"), [text_column], cap)
        if dead:
            base = len(rows)
            rows.extend(dead)
            from arrow_lake.annotation.dispatch import stable_row_id as _rid

            dead_ids = [
                _rid(str(r.get(text_column) or ""), base + i)
                for i, r in enumerate(dead)
            ]
    except Exception:  # 死信表不存在 = 无 failure_case 源
        pass
    return rows, dead_ids


async def _bg_dispatch(
    app_state: Any, lake: Any, actor: str, project: str, rows: list[dict],
    *, text_column: str, total: int, budget: SampleBudget,
    quality_scores: dict[str, float] | None, dead_row_ids: list[str] | None,
    generalize_rules: list[tuple[str, str]], entity_names: list[str],
    ls_url: str, ls_token: str, import_batch_size: int,
    ls_opener: Any = None,
) -> dict:
    """后台 worker(async;run_background 直接在主 loop await——extractor
    是 Lake 缓存组件,KG build 也在主 loop 驱动它;若在 executor 线程
    asyncio.run 新 loop 复用同一 extractor,httpx/openai 连接池的 loop
    亲和性会静默断裂 → 预测全空,甚至毒化后续 KG build。review C3)。

    ``ls_opener`` 仅测试注入(真调用默认 urllib urlopen)。
    """
    from arrow_lake.annotation.dispatch import LSClient, LSClientError, run_dispatch

    store = app_state.annotation_project_store
    rec = store.get_project(project)
    extractor_factory = getattr(lake, "_get_kg_extractor", None)
    extractor = extractor_factory() if extractor_factory else None
    # review P2: extractor 复用 KG 工厂(hugegraph.enabled gate)——KG 关闭
    # 的部署预标注静默为空。显式记进 outcome/audit,不吞。
    preannotation_mode = "hyper-extract" if extractor is not None else "skipped-no-extractor"
    if extractor is None:
        logger.warning(
            "annotation.dispatch: no KG extractor (hugegraph disabled?) — "
            "dispatching WITHOUT pre-annotations")

    def _audit(event: str, payload: dict) -> None:
        # audit best-effort,不阻塞派发结果
        with contextlib.suppress(Exception):
            lake.audit_record(event, dataset_name=rec["dataset"], actor=actor, payload=payload)

    try:
        outcome = await run_dispatch(
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
        )
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
        "strategies": outcome.strategies, "preannotation": preannotation_mode,
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
    from arrow_lake.annotation.dispatch import stable_row_id as _rid

    quality_scores = None
    if rows and "quality_score" in rows[0]:
        quality_scores = {
            _rid(str(r.get(req.text_column) or ""), i): float(r["quality_score"])
            for i, r in enumerate(rows) if r.get("quality_score") is not None
        }
    # S3: budget 白名单构造(未知键/负值 → 422,不裸 **dict)
    raw_budget = req.budget or {}
    allowed = {"uncertainty", "diversity", "failure_case", "committee"}
    unknown = set(raw_budget) - allowed
    if unknown or any(v < 0 for v in raw_budget.values()):
        raise HTTPException(
            status_code=422,
            detail=f"budget keys must be {sorted(allowed)} with values >= 0; got {dict(raw_budget)}",
        )
    budget = SampleBudget(**raw_budget)

    # S3: generalize_rules 编译预检 + 三帽(ReDoS 缓解;ADMIN 可信面的纵深)
    rules = []
    for pair in (req.generalize_rules or [])[:32]:
        if not isinstance(pair, list) or len(pair) != 2:
            raise HTTPException(status_code=422, detail=f"generalize_rules entries must be [pattern, replacement]: {pair!r}")
        pattern, replacement = str(pair[0]), str(pair[1])
        if len(pattern) > 512 or len(replacement) > 256:
            raise HTTPException(status_code=422, detail="generalize_rules pattern/replacement too long (512/256)")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise HTTPException(status_code=422, detail=f"generalize_rules invalid regex {pattern!r}: {exc}") from exc
        rules.append((pattern, replacement))

    # S1: require_masking 强制门(PII 数据集部署档)
    if config.require_masking and not req.entity_names and not rules:
        raise HTTPException(
            status_code=422,
            detail="annotation.require_masking=true — dispatch must carry entity_names or generalize_rules",
        )

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
    """手动同步(轮询对账/scheduler 共用核心,S9):LS 增量 → 五段解析 →
    仲裁(kappa/分歧)→ ADL 版本化写回(adl_id 幂等)→ 仲裁 task 生成。"""
    from arrow_lake.annotation.sync import recover_one

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
    if not rec.get("ls_project_id"):
        raise HTTPException(
            status_code=422,
            detail=f"Project '{req.project}' has no LS binding yet — dispatch first",
        )
    try:
        from arrow_lake.api.utils import ls_io_executor, run_sync

        # H13(四维 review):LS 同步 IO(urllib 10s 超时)收专用线程池——
        # 直跑 event loop 时 LS 慢/挂即冻结该 worker 全部并发请求
        summary = await run_sync(
            lambda: recover_one(
                store=store, lake=lake, config=config,
                project_name=req.project),
            timeout=600.0, label="annotation_recover",
            executor=ls_io_executor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # H2:另一 worker/scheduler 正在回收该项目
        from arrow_lake.annotation.sync import RecoverInProgress

        if isinstance(exc, RecoverInProgress):
            raise HTTPException(
                status_code=409, detail=str(exc)) from exc
        raise
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
    total = table.num_rows
    recent = table.slice(max(0, total - 50), 50).to_pylist()
    recent.reverse()  # 最近写回在前(先 slice 再物化,review S7)
    return {"project": name, "dataset": dataset, "total": total, "rows": recent}


@router.post("/webhook")
async def ls_webhook(request: Request, lake=Depends(get_lake)) -> dict:
    """LS webhook 接收(S9 加速通道;轮询对账为主)。

    认证(review S2):共享密钥 ``annotation.webhook_secret``——空 =
    端点禁用(默认安全;最小权限凭据无法伪造审计事件);LS 侧配
    webhook URL 带 ``?secret=<同值>`` 或 ``X-LS-Secret`` 头。
    C1 补丁通道落地(四维 review):ANNOTATION_CREATED/UPDATED 在审计外
    还**后台触发一轮 recover_one**(单线程串行天然去抖;adl_id 幂等吸收
    排队重复)。此前 webhook 只审计不写 ADL——docstring 宣称的补丁通道
    名不符实,复合计数判据也抓不到「同数量内容变化(草稿→提交)」。
    """
    from arrow_lake.annotation.recover import parse_webhook

    config = request.app.state.config.annotation
    supplied = request.query_params.get("secret") or request.headers.get("x-ls-secret")
    if not config.webhook_secret or supplied != config.webhook_secret:
        raise HTTPException(status_code=403, detail="webhook disabled or bad secret")

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
    # 即时回收(后台单线程;找不到匹配项目=非本项目标注,静默跳过)
    with contextlib.suppress(Exception):
        pid_raw = payload.get("project") or {}
        pid = int(pid_raw.get("id") or 0) if isinstance(pid_raw, dict) else int(pid_raw or 0)
        target = None
        if pid:
            for p in _store(request).list_projects():
                if (p.get("status") == "active"
                        and int(p.get("ls_project_id") or 0) == pid):
                    target = p["name"]
                    break
        if target:
            cfg = request.app.state.config
            _WEBHOOK_RECOVER_EXECUTOR.submit(
                _webhook_recover, request.app.state, lake, cfg, target)
    return {"accepted": True, "action": action, "row_id": rec.row_id}


# webhook 即时回收:单 worker 串行化(多条 webhook 排队,一轮全量幂等吸收)
_WEBHOOK_RECOVER_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="ls-webhook-recover")


def _webhook_recover(app_state: Any, lake: Any, cfg: Any, project: str) -> None:
    from arrow_lake.annotation.sync import recover_one

    try:
        recover_one(
            store=app_state.annotation_project_store, lake=lake,
            config=cfg.annotation, project_name=project)
    except Exception as exc:  # 兜底通道失败不抛(30s scheduler 仍是主通道)
        with contextlib.suppress(Exception):
            lake.audit_record(
                "annotation.webhook_recover_failed", dataset_name="",
                actor="ls-webhook",
                payload={"project": project, "error": str(exc)[:200]})
