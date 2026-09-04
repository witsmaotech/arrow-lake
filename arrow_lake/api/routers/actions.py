"""行动目录/场景管理+执行 API(v1.11.2 MS3 W2.3+W4.1,F3.3/S4/S5)。

管理面全部 ADMIN;执行面 EDITOR(F3.3 八步序中间件)。system_db 关闭 →
503。沿 contracts 路由约定(v1.11.0.1 W4.1)。保存期校验:YAML capped
解析 + W1 模型校验 + scenario→action 引用必须在目录(validate_scenario,
issues 一次收齐)。⚠️ /scenarios 路由先注册,否则被 /{action_id} 捕获。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from arrow_lake.actions.schema import ScenarioValidationError, validate_scenario
from arrow_lake.actions.yaml_io import ActionYamlError, parse_action_yaml, parse_scenario_yaml
from arrow_lake.api.auth_models import Role
from arrow_lake.api.deps import audit_write, get_checker, get_lake, require_role

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


def _spawn_scenario_runner(
    request: Request, *, lake, checker, user, spec, instance_id: int,
) -> None:
    """组装 runner(action 步走八步中间件闭包;assess 步走规则求值)并后台跑。"""
    import json as _json

    from arrow_lake.actions.middleware import ActionError
    from arrow_lake.actions.middleware import execute_action as _execute
    from arrow_lake.actions.runner import ScenarioRunner
    from arrow_lake.api.routers.query import _acl_enforced_sql, _deny_table_read
    from arrow_lake.api.tasks import spawn_background

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
    for step in spec.steps:
        if step.action is None or step.action in action_specs:
            continue
        rec = action_store.get_version(step.action) if action_store else None
        if rec is None:
            continue
        try:
            action_specs[step.action] = parse_action_yaml(rec["action_yaml"])
        except Exception:  # noqa: BLE001 — 腐烂条目无补偿可解析,跳过
            continue

    async def run_action(action_id: str, step_id: str) -> dict[str, Any]:
        from arrow_lake.api.deps import _deny_table_override

        target = spec_target(instance_id)
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

    def spec_target(iid: int) -> dict[str, Any]:
        rec = instance_store.get_instance(iid) or {}
        return {
            "dataset": rec.get("dataset"),
            "object_type": rec.get("object_type"),
            "object_id": rec.get("object_id"),
        }

    async def run_assess(rules_scope: str | None) -> dict[str, Any]:
        from arrow_lake.decisions.assess import evaluate_active_rules

        rec = instance_store.get_instance(instance_id) or {}
        target_ctx = _json.loads(rec.get("context_json") or "{}").get("target", {})
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
    iid = instance_store.create_instance(
        scenario_id=scenario_id,
        scenario_version=rec["version"],
        dataset=req.dataset,
        object_type=req.object_type,
        object_id=req.object_id,
        actor=actor_ctx["sub"],
        context_json=_json.dumps(
            {"target": target_ctx, "actor": actor_ctx}, ensure_ascii=False, default=str
        ),
        deadline_at=deadline_at,
    )
    audit_write(
        request, "actions.scenario_instantiated", actor=actor_ctx["sub"],
        payload={"scenario_id": scenario_id, "instance_id": iid,
                 "dataset": req.dataset, "object_id": req.object_id},
    )
    _spawn_scenario_runner(
        request, lake=lake, checker=checker, user=user, spec=spec, instance_id=iid
    )
    return {"instance_id": iid, "scenario_id": scenario_id, "status": "running"}


@router.get("/scenarios/instances", dependencies=[Depends(require_role(Role.VIEWER))])
async def list_scenario_instances(
    request: Request,
    scenario_id: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=20, ge=1, le=200),
) -> dict:
    store = _require(_instance_store(request), "Scenario instance registry")
    instances = store.list_instances(scenario_id=scenario_id, status=status, limit=limit)
    return {
        "total": len(instances),
        "instances": [_decode_instance(i) for i in instances],
    }


@router.get(
    "/scenarios/instances/{instance_id}", dependencies=[Depends(require_role(Role.VIEWER))]
)
async def get_scenario_instance(instance_id: int, request: Request) -> dict:
    import json as _json

    store = _require(_instance_store(request), "Scenario instance registry")
    rec = store.get_instance(instance_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No scenario instance {instance_id}")
    step_runs = []
    for r in store.list_step_runs(instance_id):
        run = dict(r)
        try:
            run["output"] = _json.loads(r.get("output_json") or "{}")
        except ValueError:
            run["output"] = {}
        step_runs.append(run)
    out = _decode_instance(rec)
    try:
        out["context"] = _json.loads(rec.get("context_json") or "{}")
    except ValueError:
        out["context"] = {}
    return {"instance": out, "step_runs": step_runs}


@router.post(
    "/scenarios/instances/{instance_id}/terminate",
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def terminate_scenario_instance(
    instance_id: int, request: Request, user=Depends(require_role(Role.ADMIN))
) -> dict:
    """运行中 → terminated(runner 下一轮循环退出;在途步不中断)。"""
    store = _require(_instance_store(request), "Scenario instance registry")
    rec = store.get_instance(instance_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No scenario instance {instance_id}")
    if rec["status"] != "running":
        raise HTTPException(
            status_code=409, detail=f"instance {instance_id} is '{rec['status']}', not running"
        )
    store.update_instance(instance_id, status="terminated", finished=True)
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
    rec = instance_store.get_instance(instance_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No scenario instance {instance_id}")
    if rec["status"] not in ("failed", "timeout", "compensated", "terminated"):
        raise HTTPException(
            status_code=409,
            detail=f"instance {instance_id} is '{rec['status']}' (resumable: "
            f"failed/timeout/compensated/terminated)",
        )
    srec = scenario_store.get_version(rec["scenario_id"])
    if srec is None:
        raise HTTPException(
            status_code=422,
            detail=f"scenario '{rec['scenario_id']}' no longer exists; cannot resume",
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
    instance_store.update_instance(
        instance_id, status="running", error=None, deadline_at=deadline_at or "", reopen=True
    )
    audit_write(request, "actions.scenario_resumed", actor=user.sub,
                payload={"instance_id": instance_id})
    _spawn_scenario_runner(
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
