"""行动目录/场景管理+执行 API(v1.11.2 MS3 W2.3+W4.1,F3.3/S4/S5)。

管理面全部 ADMIN;执行面 EDITOR(F3.3 八步序中间件)。system_db 关闭 →
503。沿 contracts 路由约定(v1.11.0.1 W4.1)。保存期校验:YAML capped
解析 + W1 模型校验 + scenario→action 引用必须在目录(validate_scenario,
issues 一次收齐)。⚠️ /scenarios 路由先注册,否则被 /{action_id} 捕获。
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from arrow_lake.actions.schema import ScenarioValidationError, validate_scenario
from arrow_lake.actions.yaml_io import ActionYamlError, parse_action_yaml, parse_scenario_yaml
from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import audit_write, get_checker, get_lake, require_role

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/actions", tags=["actions"])


def _action_store(request: Request) -> Any:
    return getattr(request.app.state, "action_store", None)


def _scenario_store(request: Request) -> Any:
    return getattr(request.app.state, "scenario_store", None)


def _require(store: Any, what: str) -> Any:
    if store is None:
        raise HTTPException(status_code=503, detail=f"{what} unavailable (system_db disabled)")
    return store


class ActionUpsertRequest(BaseModel):
    action_yaml: str = Field(min_length=1, max_length=200_000)


class ScenarioUpsertRequest(BaseModel):
    scenario_yaml: str = Field(min_length=1, max_length=200_000)


# --------------------------------------------------------------------------- #
# scenarios(先注册——见模块 docstring)                                        #
# --------------------------------------------------------------------------- #


@router.get("/scenarios", dependencies=[Depends(require_role(Role.ADMIN))])
async def list_scenarios(request: Request) -> dict:
    """List scenario scopes with their latest version summary."""
    store = _require(_scenario_store(request), "Scenario registry")
    scopes = store.list_scopes()
    return {
        "total": len(scopes),
        "scenarios": [
            {
                "scenario_id": s["scope"],
                "version": s["version"],
                "source_hash": s["source_hash"],
                "updated_at": s["created_at"],
            }
            for s in scopes
        ],
    }


# --------------------------------------------------------------------------- #
# scenario 执行(v1.11.5 W3,S7/S8/S9)                                        #
# ⚠️ /scenarios/instances* 必须先于 /scenarios/{scenario_id} 注册,          #
#    否则 GET /scenarios/instances 被 scenario_id="instances" 捕获。          #
# --------------------------------------------------------------------------- #


class InstantiateRequest(BaseModel):
    dataset: str = Field(min_length=1, max_length=200)
    object_type: str = Field(min_length=1, max_length=200)
    object_id: str = Field(min_length=1, max_length=500)
    reason: str | None = Field(default=None, max_length=2000)


def _instance_store(request: Request) -> Any:
    return getattr(request.app.state, "scenario_instance_store", None)


def _decode_instance(rec: dict) -> dict:
    import json as _json

    out = dict(rec)
    out["pending_compensation"] = _json.loads(rec.get("pending_compensation_json") or "[]")
    return out


def _pruned_target_ctx(
    request: Request, user: Any, rec: dict, ctx: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """H-3②(v1.11.6.6 收敛):实例读按调用者复查列级 ACL。

    返回 (ctx, restricted):restricted=True 表示 target 被裁/被清——调用方
    须同步骨架化 ctx["steps"] 与 step_runs 的 output/error(安全 H-1:幂等
    键模板可嵌隐藏列明文、错误串可嵌单元格值,无法按列裁,只能整体剥)。

    - 调用者无 dataset 读权(deny/ACL)→ target 置空+acl_pruned;
    - 列受限 → target 裁到可见列集。行过滤语义不下推(单行,声明不裁)。
    - 容器表修复(质量 H-1):caller_visible_columns 只传 dataset——
      object_type 是 Object Set 语义类型非物理表名,误传会让二段名 ACL
      查找拼成 "gas.segments.alerts" 三段而 miss → fail-open。
    - 表级 deny(安全 M-1):dataset 自身作为二段键补查(容器表
      "ds.table" 形态的表级 deny 在 check_dataset_access 的 dataset 键
      查找之外)。
    """
    from arrow_lake.api.deps import _deny_table_override, caller_visible_columns

    dataset = rec.get("dataset") or ""
    target = ctx.get("target")
    if not dataset or not isinstance(target, dict) or not target:
        return ctx, False
    if getattr(user, "role", None) == Role.ADMIN:
        return ctx, False
    checker = get_checker(request)
    perms = getattr(user, "permissions", None) or None
    if not checker.check_dataset_access(
        role=user.role, dataset=dataset, action="read", permissions=perms
    ):
        return {**ctx, "target": {}, "acl_pruned": True}, True
    if _deny_table_override(request, dataset, write=False):
        return {**ctx, "target": {}, "acl_pruned": True}, True
    allowed = caller_visible_columns(request, dataset)
    if allowed is None:
        return ctx, False
    kept = {k: v for k, v in target.items() if k.lower() in allowed}
    if len(kept) == len(target):
        return ctx, False
    return {**ctx, "target": kept, "acl_pruned": True}, True


async def _fetch_target_ctx(
    *, lake, checker, user, dataset: str, object_type: str, object_id: str,
    contract_store, alignment_store, request,
) -> dict[str, Any]:
    """共享取数管线取目标对象 → target 上下文(404/403 语义同 objects)。"""
    from arrow_lake.api.routers.query import _acl_enforced_sql, _deny_table_read
    from arrow_lake.semantic.objectset import fetch_object_rows

    res = await fetch_object_rows(
        lake=lake,
        checker=checker,
        role=user.role,
        permissions=getattr(user, "permissions", None),
        dataset=dataset,
        object_type=object_type,
        object_id=object_id,
        limit=2,
        contract_store=contract_store,
        alignment_store=alignment_store,
        deny_table_read=lambda n, t: _deny_table_read(n, t, request),
        acl_enforce=lambda sql, tgt: _acl_enforced_sql(sql, tgt, checker, user.role),
    )
    if not res.rows:
        raise HTTPException(
            status_code=404, detail=f"object '{object_id}' not found in {dataset}.{object_type}"
        )
    if len(res.rows) > 1:
        raise HTTPException(
            status_code=422,
            detail=f"object '{object_id}' resolves to {len(res.rows)} rows — "
            f"identifier not unique",
        )
    target_ctx: dict[str, Any] = dict(res.rows[0])
    if res.lifecycle_col is not None and res.lifecycle_col in res.rows[0]:
        target_ctx["lifecycle_state"] = res.rows[0][res.lifecycle_col]
    target_ctx["object_id"] = object_id
    return target_ctx


async def _spawn_scenario_runner(
    request: Request, *, lake, checker, user, spec, instance_id: int,
) -> None:
    """组装 runner(action 步走八步中间件闭包;assess 步走规则求值)并后台跑。

    async 化(性能 M-1 收敛):本函数在路由协程里直跑,内部的 store 远端
    IO(action 目录批量读取/实例行)须下线程,不再阻塞 worker 事件循环。
    """
    import json as _json

    from arrow_lake.actions.middleware import ActionError
    from arrow_lake.actions.middleware import execute_action as _execute
    from arrow_lake.actions.runner import ScenarioRunner
    from arrow_lake.api.routers.query import _acl_enforced_sql, _deny_table_read
    from arrow_lake.api.tasks import spawn_background
    from arrow_lake.api.utils import run_sync

    action_store = getattr(request.app.state, "action_store", None)
    idempotency_store = getattr(request.app.state, "idempotency_store", None)
    contract_store = getattr(request.app.state, "contract_store", None)
    alignment_store = getattr(request.app.state, "semantic_alignment_store", None)
    user_state_store = getattr(request.app.state, "user_state_store", None)
    rules_store = getattr(request.app.state, "ontology_rules_store", None)
    scenario_store = _scenario_store(request)
    instance_store = _instance_store(request)

    # 补偿解析:场景引用的 action 目录条目(失败步的 compensation 声明)
    from arrow_lake.actions.yaml_io import parse_action_yaml

    action_specs: dict[str, Any] = {}

    def _load_action_specs() -> None:
        for step in spec.steps:
            if step.action is None or step.action in action_specs:
                continue
            rec = action_store.get_version(step.action) if action_store else None
            if rec is None:
                continue
            try:
                action_specs[step.action] = parse_action_yaml(rec["action_yaml"])
            except Exception:  # 腐烂条目无补偿可解析,跳过
                continue

    await run_sync(_load_action_specs, label="scenario_action_specs")

    # H-2:实例行是版本 SoT——middleware 场景归属校验按锚定版本钉住
    # (升版后旧实例续跑不再被最新版的 step 集合误拒)。
    inst_rec = await run_sync(
        lambda: instance_store.get_instance(instance_id),
        label="scenario_pinned_version",
    )
    pinned_version = (inst_rec or {}).get("scenario_version")
    if pinned_version is None:
        # LOW(收敛):回落无痕原为静默——留痕便于排障(middleware 将按
        # 最新版校验,极端窗口下实例行刚被清才走到这)
        logger.warning(
            "scenario_pinned_version_missing",
            extra={"instance": instance_id, "scenario": spec.scenario_id},
        )

    async def run_action(action_id: str, step_id: str) -> dict[str, Any]:
        from arrow_lake.api.deps import _deny_table_override

        target = await _target_of(instance_id)
        try:
            return await _execute(
                lake=lake,
                checker=checker,
                user=user,
                action_id=action_id,
                dataset=target["dataset"] or "",
                object_type=target["object_type"] or "",
                object_id=target["object_id"] or "",
                reason=f"scenario {spec.scenario_id} step {step_id}",
                scenario_id=spec.scenario_id,
                step_id=step_id,
                scenario_version=pinned_version,  # H-2:归属校验钉实例锚定版
                action_store=action_store,
                idempotency_store=idempotency_store,
                contract_store=contract_store,
                alignment_store=alignment_store,
                user_state_store=user_state_store,
                rules_store=rules_store,
                scenario_store=scenario_store,
                deny_table_read=lambda n, t: _deny_table_read(n, t, request),
                acl_enforce=lambda sql, tgt: _acl_enforced_sql(sql, tgt, checker, user.role),
                deny_table_write=lambda n, t: (
                    _deny_table_override(request, f"{n}.{t}", write=True) if t else None
                ),
            )
        except ActionError as exc:  # REJECT 语义 → 步 failed(runner 捕获落行)
            return {"status": "failed", "error": exc.reason,
                    "exception_class": exc.exception_class}

    async def _target_of(iid: int) -> dict[str, Any]:
        rec = await run_sync(
            lambda: instance_store.get_instance(iid), label="scenario_spec_target"
        )
        rec = rec or {}
        return {
            "dataset": rec.get("dataset"),
            "object_type": rec.get("object_type"),
            "object_id": rec.get("object_id"),
        }

    async def run_assess(rules_scope: str | None) -> dict[str, Any]:
        from arrow_lake.decisions.assess import evaluate_active_rules

        rec = await run_sync(
            lambda: instance_store.get_instance(instance_id),
            label="scenario_assess_ctx",
        )
        rec = rec or {}
        try:
            target_ctx = _json.loads(rec.get("context_json") or "{}").get("target", {})
        except ValueError:
            target_ctx = {}
        dataset = rec.get("dataset") or ""
        if rules_store is None or not dataset:
            return {"conclusions": [], "unruly": []}
        conclusions, unruly = await evaluate_active_rules(rules_store, dataset, target_ctx)
        return {"conclusions": conclusions, "unruly": unruly}

    runner = ScenarioRunner(
        spec=spec,
        store=instance_store,
        instance_id=instance_id,
        run_action=run_action,
        run_assess=run_assess,
        action_specs=action_specs,
    )
    spawn_background(runner.run())


@router.post(
    "/scenarios/{scenario_id}/instantiate",
    status_code=202,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def instantiate_scenario(
    scenario_id: str,
    req: InstantiateRequest,
    request: Request,
    lake=Depends(get_lake),
    user=Depends(require_role(Role.EDITOR)),
    checker=Depends(get_checker),
) -> dict:
    """实例化并后台执行:校验最新版+引用 → 目标取数 → entries 求值 →
    建实例 → 202 {instance_id}(runner spawn_background 强引用)。"""
    import json as _json
    from datetime import UTC, datetime, timedelta

    from arrow_lake.actions.predicates import ParsedPredicateError, compile_predicate
    from arrow_lake.actions.runner import parse_iso_duration

    store = _require(_scenario_store(request), "Scenario registry")
    action_store = _require(_action_store(request), "Action catalog")
    instance_store = _require(_instance_store(request), "Scenario instance registry")
    idempotency_store = _require(
        getattr(request.app.state, "idempotency_store", None), "Idempotency registry"
    )
    contract_store = getattr(request.app.state, "contract_store", None)
    if contract_store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; contracts unavailable")

    rec = store.get_version(scenario_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No scenario '{scenario_id}'")
    try:
        spec = parse_scenario_yaml(rec["scenario_yaml"])
    except ActionYamlError as exc:
        raise HTTPException(422, f"Scenario '{scenario_id}' unparseable: {exc}") from exc
    known = {s["scope"] for s in action_store.list_scopes()}
    try:
        validate_scenario(spec, known)
    except ScenarioValidationError as exc:
        raise HTTPException(
            422,
            detail={"message": "scenario references unresolvable", "issues": exc.issues},
        ) from exc

    target_ctx = await _fetch_target_ctx(
        lake=lake, checker=checker, user=user, dataset=req.dataset,
        object_type=req.object_type, object_id=req.object_id,
        contract_store=contract_store,
        alignment_store=getattr(request.app.state, "semantic_alignment_store", None),
        request=request,
    )

    # M9(v1.11.6.6 收敛,性能 H-2):同 (scenario,dataset,object) 已有
    # running 实例 → 409。查重下推 SQL(EXISTS)——原实现拉 200 行全字段
    # (含 context_json)内存比对,>200 running 时盲区漏检双活;V028 部分
    # 唯一索引在 DB 层封死 TOCTOU 窗口(INSERT 撞索引也转 409)。
    from arrow_lake.api.utils import run_sync

    dup = await run_sync(
        lambda: instance_store.exists_running(
            scenario_id=scenario_id, dataset=req.dataset,
            object_type=req.object_type, object_id=req.object_id,
        ),
        label="scenario_dup_check",
    )
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"scenario '{scenario_id}' already running for object "
                f"'{req.object_id}' (instance {dup['id']}); resume or wait "
                "for a terminal state"
            ),
        )

    # entries 求值(任一真即可入;空 entries 无门)
    if spec.entries:
        entry_ctx = {"target": target_ctx}
        matched = False
        try:
            matched = any(compile_predicate(e).evaluate(entry_ctx) for e in spec.entries)
        except ParsedPredicateError:
            matched = False
        if not matched:
            raise HTTPException(
                422,
                detail=f"no scenario entry matched for object '{req.object_id}' "
                f"(entries: {list(spec.entries)})",
            )

    deadline_at: str | None = None
    if spec.timeout is not None:
        try:
            deadline = datetime.now(UTC) + timedelta(seconds=parse_iso_duration(spec.timeout))
            deadline_at = deadline.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            deadline_at = None

    actor_ctx = {
        "sub": getattr(user, "sub", ""),
        "role": str(getattr(user.role, "value", user.role)),
    }
    try:
        iid = await run_sync(
            lambda: instance_store.create_instance(
                scenario_id=scenario_id,
                scenario_version=rec["version"],
                dataset=req.dataset,
                object_type=req.object_type,
                object_id=req.object_id,
                actor=actor_ctx["sub"],
                context_json=_json.dumps(
                    {"target": target_ctx, "actor": actor_ctx},
                    ensure_ascii=False, default=str,
                ),
                deadline_at=deadline_at,
            ),
            label="scenario_instance_create",
        )
    except Exception as exc:  # V028 唯一索引兜底 TOCTOU 窗口的并发 INSERT
        if "UNIQUE" in str(exc):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"scenario '{scenario_id}' already running for object "
                    f"'{req.object_id}' (concurrent instantiate); resume or "
                    "wait for a terminal state"
                ),
            ) from exc
        raise
    audit_write(
        request, "actions.scenario_instantiated", actor=actor_ctx["sub"],
        payload={"scenario_id": scenario_id, "instance_id": iid,
                 "dataset": req.dataset, "object_id": req.object_id},
    )
    await _spawn_scenario_runner(
        request, lake=lake, checker=checker, user=user, spec=spec, instance_id=iid
    )
    return {"instance_id": iid, "scenario_id": scenario_id, "status": "running"}


@router.get("/scenarios/instances")
async def list_scenario_instances(
    request: Request,
    user=Depends(require_role(Role.VIEWER)),
    scenario_id: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    from arrow_lake.api.utils import run_sync

    store = _require(_instance_store(request), "Scenario instance registry")
    instances = await run_sync(
        lambda: store.list_instances(
            scenario_id=scenario_id, status=status, limit=limit, offset=offset,
            include_context=False,  # 性能 H-1:列表不取 context_json
        ),
        label="scenario_instances_list",
    )
    # H-3②(v1.11.6.6):列表不回 context_json——完整 target 走详情端点
    # (那里按调用者 ACL 裁剪),列表行逐行裁剪既重又不必要。
    rows = [
        {k: v for k, v in _decode_instance(i).items() if k != "context_json"}
        for i in instances
    ]
    # 安全 M-1(收敛):非 ADMIN 按 dataset 读权过滤——整库拒读用户不得
    # 经实例列表枚举 dataset/object_id 对象标识;checker 调用按 distinct
    # dataset 去重(通常 1-3 个)。total 保守保持全局计数(翻页稳定)。
    if getattr(user, "role", None) != Role.ADMIN:
        checker = get_checker(request)
        perms = getattr(user, "permissions", None) or None

        def _visible_dataset(ds: str, cache: dict[str, bool]) -> bool:
            if ds not in cache:
                cache[ds] = checker.check_dataset_access(
                    role=user.role, dataset=ds, action="read", permissions=perms
                )
            return cache[ds]

        cache: dict[str, bool] = {}
        rows = [r for r in rows if _visible_dataset(r.get("dataset") or "", cache)]
    total = await run_sync(
        lambda: store.count_instances(scenario_id=scenario_id, status=status),
        label="scenario_instances_count",
    )
    return {"total": total, "limit": limit, "offset": offset, "instances": rows}


@router.get("/scenarios/instances/{instance_id}")
async def get_scenario_instance(
    instance_id: int, request: Request, user=Depends(require_role(Role.VIEWER))
) -> dict:
    import json as _json

    from arrow_lake.api.utils import run_sync

    store = _require(_instance_store(request), "Scenario instance registry")
    rec = await run_sync(
        lambda: store.get_instance(instance_id), label="scenario_instance_get"
    )
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No scenario instance {instance_id}")
    step_recs = await run_sync(
        lambda: store.list_step_runs(instance_id), label="scenario_step_runs"
    )
    out = _decode_instance(rec)
    out.pop("context_json", None)  # 原始串不可旁路下面的裁剪
    try:
        ctx = _json.loads(rec.get("context_json") or "{}")
    except ValueError:
        ctx = {}
    ctx, restricted = _pruned_target_ctx(request, user, rec, ctx)
    step_runs = []
    for r in step_recs:
        run = dict(r)
        # 安全 H-1(收敛):target 受限(被裁/被清)时,步 output 与 error
        # 同步骨架化——幂等键模板可嵌隐藏列明文、错误串可嵌单元格值,
        # 无法按列裁只能整体剥(时间线 status/kind 保留,排障可用)。
        if restricted:
            run["output"] = {"acl_pruned": True}
            run["error"] = None
        else:
            try:
                run["output"] = _json.loads(r.get("output_json") or "{}")
            except ValueError:
                run["output"] = {}
        step_runs.append(run)
    if restricted:
        ctx = {**ctx, "steps": {}}
    out["context"] = ctx
    return {"instance": out, "step_runs": step_runs}


@router.post(
    "/scenarios/instances/{instance_id}/compensation/{step_id}/ack",
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def ack_scenario_compensation(
    instance_id: int,
    step_id: str,
    request: Request,
    user=Depends(require_role(Role.EDITOR)),
) -> dict:
    """M8(v1.11.6.6):人工补偿核销——清该步声明的 pending 项。

    实例级 ``pending_compensation`` 原是 append-only(待办只增不减,补偿
    action 无 idem key 时重复点击=双补偿);核销以步为单位:步行走
    ``output.pending_compensation`` 声明,核销即从实例清单剔除。

    收敛(v1.11.6.6):①CAS 条件写——并发核销不再互相复活对方已清项;
    ②dataset 读权门禁——核销者须能读该实例的数据集(治理写仍留审计);
    ③JSON 腐烂统一 422 带原因(原:实例侧 500 / 步侧误 409)。
    """
    import json as _json

    from arrow_lake.api.utils import run_sync

    instance_store = _require(_instance_store(request), "Scenario instance registry")
    rec = await run_sync(
        lambda: instance_store.get_instance(instance_id),
        label="scenario_instance_get",
    )
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No scenario instance {instance_id}")
    # 安全 L-2(收敛):治理写也要过 dataset 读权(整库拒读者不应能操作
    # 看不见的数据集的补偿生命周期)
    ds = rec.get("dataset") or ""
    if ds and getattr(user, "role", None) != Role.ADMIN:
        checker = get_checker(request)
        if not checker.check_dataset_access(
            role=user.role, dataset=ds, action="read",
            permissions=getattr(user, "permissions", None) or None,
        ):
            raise HTTPException(status_code=403, detail="no read access to dataset")
    raw_pending = rec.get("pending_compensation_json")
    try:
        pending = _json.loads(raw_pending or "[]")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"instance {instance_id} pending_compensation is corrupt",
        ) from None
    if not pending:
        raise HTTPException(
            status_code=409,
            detail=f"instance {instance_id} has no pending compensation",
        )
    steps = await run_sync(
        lambda: instance_store.list_step_runs(instance_id),
        label="scenario_step_runs",
    )
    step = next((r for r in steps if r["step_id"] == step_id), None)
    if step is None:
        raise HTTPException(
            status_code=404, detail=f"No step '{step_id}' on instance {instance_id}"
        )
    try:
        declared = list(
            _json.loads(step.get("output_json") or "{}").get("pending_compensation") or []
        )
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"step '{step_id}' output is corrupt (cannot determine declared "
            "compensation)",
        ) from None
    if not declared:
        raise HTTPException(
            status_code=409, detail=f"step '{step_id}' declared no compensation"
        )
    acked = set(declared)
    remaining = [a for a in pending if a not in acked]
    if remaining == pending:
        raise HTTPException(
            status_code=409,
            detail=f"compensation declared by step '{step_id}' already acknowledged",
        )
    ok = await run_sync(
        lambda: instance_store.ack_pending(
            instance_id, expect_json=raw_pending, remaining=remaining
        ),
        label="scenario_compensation_ack",
    )
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="pending compensation changed concurrently; re-read and retry",
        )
    audit_write(
        request, "actions.scenario_compensation_acked", actor=user.sub,
        payload={"instance_id": instance_id, "step_id": step_id,
                 "acked": declared, "remaining": remaining},
    )
    return {
        "instance_id": instance_id,
        "step_id": step_id,
        "acked": declared,
        "pending_compensation": remaining,
    }


@router.post(
    "/scenarios/instances/{instance_id}/terminate",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def terminate_scenario_instance(
    instance_id: int, request: Request, user=Depends(require_role(Role.ADMIN))
) -> dict:
    """运行中 → terminated(runner 下一轮循环退出;在途步不中断)。

    LOW(v1.11.6.6):CAS 写——读-写窗口内实例自行终态(completed/failed)
    时不覆写。"""
    store = _require(_instance_store(request), "Scenario instance registry")
    from arrow_lake.api.utils import run_sync

    rec = await run_sync(
        lambda: store.get_instance(instance_id), label="scenario_instance_get"
    )
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No scenario instance {instance_id}")
    if rec["status"] != "running":
        raise HTTPException(
            status_code=409, detail=f"instance {instance_id} is '{rec['status']}', not running"
        )
    if not await run_sync(
        lambda: store.terminate_instance(instance_id),
        label="scenario_instance_terminate",
    ):
        raise HTTPException(
            status_code=409,
            detail=f"instance {instance_id} reached a terminal state concurrently",
        )
    audit_write(request, "actions.scenario_terminated", actor=user.sub,
                payload={"instance_id": instance_id})
    return {"instance_id": instance_id, "status": "terminated"}


@router.post(
    "/scenarios/instances/{instance_id}/resume",
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def resume_scenario_instance(
    instance_id: int,
    request: Request,
    lake=Depends(get_lake),
    user=Depends(require_role(Role.EDITOR)),
    checker=Depends(get_checker),
) -> dict:
    """断点续跑:终态(failed/timeout/compensated/terminated)→ running,
    deadline 重算,runner 重入(assess 重跑;崩溃窗口步经幂等重放)。"""
    from datetime import UTC, datetime, timedelta

    from arrow_lake.actions.runner import parse_iso_duration

    scenario_store = _require(_scenario_store(request), "Scenario registry")
    instance_store = _require(_instance_store(request), "Scenario instance registry")
    from arrow_lake.api.utils import run_sync

    rec = await run_sync(
        lambda: instance_store.get_instance(instance_id),
        label="scenario_instance_get",
    )
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No scenario instance {instance_id}")
    resumable = instance_store.RESUMABLE_STATUSES  # 单源(store CAS WHERE 同词表)
    if rec["status"] not in resumable:
        raise HTTPException(
            status_code=409,
            detail=f"instance {instance_id} is '{rec['status']}' (resumable: "
            f"{', '.join(resumable)})",
        )
    # H-2(v1.11.6.6):钉实例锚定版本——升版后的新 spec 不混入续跑语义
    # (要用新版本须另 instantiate;middleware 归属校验同钉,见 run_action)。
    srec = await run_sync(
        lambda: scenario_store.get_version(
            rec["scenario_id"], version=rec["scenario_version"]
        ),
        label="scenario_pinned_spec",
    )
    if srec is None:
        raise HTTPException(
            status_code=422,
            detail=f"scenario '{rec['scenario_id']}' version "
            f"{rec['scenario_version']} no longer exists; cannot resume "
            "(instantiate a new instance to run the current version)",
        )
    try:
        spec = parse_scenario_yaml(srec["scenario_yaml"])
    except ActionYamlError as exc:
        raise HTTPException(422, f"Scenario unparseable: {exc}") from exc

    deadline_at: str | None = None
    if spec.timeout is not None:
        try:
            deadline = datetime.now(UTC) + timedelta(seconds=parse_iso_duration(spec.timeout))
            deadline_at = deadline.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            deadline_at = None
    # M9:CAS 终态→running(并发 resume/terminate 竞争仅一方成功,防双活)
    from arrow_lake.api.utils import run_sync

    if not await run_sync(
        lambda: instance_store.resume_instance(
            instance_id, deadline_at=deadline_at or ""
        ),
        label="scenario_instance_resume",
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"instance {instance_id} changed state concurrently and is no "
                "longer resumable (re-read and retry)"
            ),
        )
    audit_write(request, "actions.scenario_resumed", actor=user.sub,
                payload={"instance_id": instance_id})
    await _spawn_scenario_runner(
        request, lake=lake, checker=checker, user=user, spec=spec, instance_id=instance_id
    )
    return {"instance_id": instance_id, "status": "running"}


@router.get("/scenarios/{scenario_id}", dependencies=[Depends(require_role(Role.ADMIN))])
async def get_scenario(scenario_id: str, request: Request) -> dict:
    store = _require(_scenario_store(request), "Scenario registry")
    rec = store.get_version(scenario_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No scenario '{scenario_id}'")
    return {"scenario_id": scenario_id, **rec}


@router.get(
    "/scenarios/{scenario_id}/versions",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def list_scenario_versions(scenario_id: str, request: Request) -> dict:
    store = _require(_scenario_store(request), "Scenario registry")
    versions = store.list_versions(scenario_id)
    return {"scenario_id": scenario_id, "total": len(versions), "versions": versions}


@router.put(
    "/scenarios/{scenario_id}",
    status_code=200,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def save_scenario(scenario_id: str, req: ScenarioUpsertRequest, request: Request) -> dict:
    """Save a scenario: capped parse → 模型校验 → 引用校验(steps 引用的
    action 必须在行动目录)→ 版本链保存(同 hash 跳过)。"""
    store = _require(_scenario_store(request), "Scenario registry")
    action_store = _require(_action_store(request), "Action catalog")
    try:
        spec = parse_scenario_yaml(req.scenario_yaml)
    except ActionYamlError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid scenario: {exc}") from exc
    if spec.scenario_id != scenario_id:
        raise HTTPException(
            status_code=422,
            detail=(f"scenario_id field ({spec.scenario_id!r}) must match path ({scenario_id!r})"),
        )
    known = {s["scope"] for s in action_store.list_scopes()}
    try:
        validate_scenario(spec, known)
    except ScenarioValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "scenario references unresolvable", "issues": exc.issues},
        ) from exc
    rec = store.save_scenario(scenario_id, req.scenario_yaml)
    return {"scenario_id": scenario_id, **rec}


@router.delete(
    "/scenarios/{scenario_id}",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def delete_scenario(scenario_id: str, request: Request,
                              user=Depends(require_role(Role.ADMIN))) -> dict:
    store = _require(_scenario_store(request), "Scenario registry")
    if not store.delete_scope(scenario_id):
        raise HTTPException(status_code=404, detail=f"No scenario '{scenario_id}'")
    audit_write(request, "actions.scenario_deleted", actor=user.sub,
                payload={"scenario_id": scenario_id})
    return {"scenario_id": scenario_id, "deleted": True}


# --------------------------------------------------------------------------- #
# actions catalog                                                              #
# --------------------------------------------------------------------------- #


@router.get("", dependencies=[Depends(require_role(Role.ADMIN))])
async def list_actions(request: Request) -> dict:
    """List catalog action ids with their latest version summary."""
    store = _require(_action_store(request), "Action catalog")
    scopes = store.list_scopes()
    return {
        "total": len(scopes),
        "actions": [
            {
                "action_id": s["scope"],
                "version": s["version"],
                "source_hash": s["source_hash"],
                "updated_at": s["created_at"],
            }
            for s in scopes
        ],
    }


@router.get("/{action_id}", dependencies=[Depends(require_role(Role.ADMIN))])
async def get_action(action_id: str, request: Request) -> dict:
    store = _require(_action_store(request), "Action catalog")
    rec = store.get_version(action_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No action '{action_id}'")
    return {"action_id": action_id, **rec}


@router.get("/{action_id}/versions", dependencies=[Depends(require_role(Role.ADMIN))])
async def list_action_versions(action_id: str, request: Request) -> dict:
    store = _require(_action_store(request), "Action catalog")
    versions = store.list_versions(action_id)
    return {"action_id": action_id, "total": len(versions), "versions": versions}


@router.get(
    "/{action_id}/versions/{version}",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def get_action_version(action_id: str, version: int, request: Request) -> dict:
    store = _require(_action_store(request), "Action catalog")
    rec = store.get_version(action_id, version=version)
    if rec is None:
        raise HTTPException(
            status_code=404, detail=f"No action version {version} for '{action_id}'"
        )
    return {"action_id": action_id, **rec}


@router.put(
    "/{action_id}",
    status_code=200,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def save_action(action_id: str, req: ActionUpsertRequest, request: Request) -> dict:
    """Save an action: capped parse → 模型校验(effect 封闭集/模板/谓词)
    → 版本链保存(同 hash 跳过)。"""
    store = _require(_action_store(request), "Action catalog")
    try:
        spec = parse_action_yaml(req.action_yaml)
    except ActionYamlError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid action: {exc}") from exc
    if spec.action_id != action_id:
        raise HTTPException(
            status_code=422,
            detail=(f"action_id field ({spec.action_id!r}) must match path ({action_id!r})"),
        )
    rec = store.save_action(action_id, req.action_yaml)
    return {"action_id": action_id, **rec}


@router.delete("/{action_id}", dependencies=[Depends(require_role(Role.ADMIN))])
async def delete_action(action_id: str, request: Request,
                          user=Depends(require_role(Role.ADMIN))) -> dict:
    """Delete an action (all versions). Scenarios referencing it fail their
    next save — save-time reference discipline, no cascade."""
    store = _require(_action_store(request), "Action catalog")
    if not store.delete_scope(action_id):
        raise HTTPException(status_code=404, detail=f"No action '{action_id}'")
    audit_write(request, "actions.action_deleted", actor=user.sub,
                payload={"action_id": action_id})
    return {"action_id": action_id, "deleted": True}


# --------------------------------------------------------------------------- #
# 执行(F3.3 八步序中间件;EDITOR——行动才是 EDITOR,S9 的写侧)            #
# --------------------------------------------------------------------------- #


class ExecuteRequest(BaseModel):
    dataset: str = Field(min_length=1, max_length=200)
    object_type: str = Field(min_length=1, max_length=200)
    object_id: str = Field(min_length=1, max_length=500)
    reason: str | None = Field(
        default=None, max_length=2000, description="执行理由(审计;reason_required 时必填)"
    )
    scenario_id: str | None = Field(default=None, max_length=200)
    step_id: str | None = Field(default=None, max_length=200)
    assess: dict[str, Any] | None = Field(
        default=None,
        description="(兼容保留)调用方回显;W4.5 H-3 起服务端对 active 规则"
        "重评 canonical 字段,客户端值不进入任何信任面",
    )


@router.post("/{action_id}/execute", dependencies=[Depends(require_role(Role.EDITOR))])
async def execute_action(
    action_id: str,
    req: ExecuteRequest,
    request: Request,
    lake=Depends(get_lake),
    _user=Depends(require_role(Role.EDITOR)),
    checker=Depends(get_checker),
) -> dict:
    """Execute an action against one object(八步序:认证→permission→目标
    解析(+写向门禁)→幂等→前置→效果→审计→事件)。重放 → 200
    already_in_effect;assess 上下文由服务端重评(不可伪造)。"""
    from arrow_lake.actions.middleware import ActionError
    from arrow_lake.actions.middleware import execute_action as _execute
    from arrow_lake.api.deps import _deny_table_override
    from arrow_lake.api.routers.query import _acl_enforced_sql, _deny_table_read

    action_store = _require(_action_store(request), "Action catalog")
    idempotency_store = _require(
        getattr(request.app.state, "idempotency_store", None), "Idempotency registry"
    )
    contract_store = getattr(request.app.state, "contract_store", None)
    if contract_store is None:
        raise HTTPException(status_code=503, detail="system_db disabled; contracts unavailable")
    alignment_store = getattr(request.app.state, "semantic_alignment_store", None)
    user_state_store = getattr(request.app.state, "user_state_store", None)
    rules_store = getattr(request.app.state, "ontology_rules_store", None)
    scenario_store = _scenario_store(request)

    try:
        return await _execute(
            lake=lake,
            checker=checker,
            user=_user,
            action_id=action_id,
            dataset=req.dataset,
            object_type=req.object_type,
            object_id=req.object_id,
            reason=req.reason,
            scenario_id=req.scenario_id,
            step_id=req.step_id,
            assess=req.assess,
            action_store=action_store,
            idempotency_store=idempotency_store,
            contract_store=contract_store,
            alignment_store=alignment_store,
            user_state_store=user_state_store,
            rules_store=rules_store,
            scenario_store=scenario_store,
            deny_table_read=lambda n, t: _deny_table_read(n, t, request),
            acl_enforce=lambda sql, tgt: _acl_enforced_sql(sql, tgt, checker, _user.role),
            deny_table_write=lambda n, t: (
                _deny_table_override(request, f"{n}.{t}", write=True) if t else None
            ),
        )
    except ActionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"message": exc.reason, "exception_class": exc.exception_class},
        ) from exc


@router.post(
    "/{action_id}/idempotency/reset",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def reset_idempotency_slot(
    action_id: str,
    request: Request,
    key: str = Query(min_length=1, max_length=500),
    lake=Depends(get_lake),
    user=Depends(require_role(Role.ADMIN)),
) -> dict:
    """ADMIN 手术:重置卡死 running 的幂等槽(worker 在 acquire 与 mark
    之间死亡遗留;W4.5 H-2 运维面,沿 tasks.py orphan-reap 教训人工核销)。"""
    store = _require(getattr(request.app.state, "idempotency_store", None), "Idempotency registry")
    reset = store.reset_running(action_id, key)
    audit_write(request, "actions.idempotency_reset", actor=user.sub,
                payload={"action_id": action_id, "key": key, "reset": reset})
    return {"action_id": action_id, "key": key, "reset": reset}
