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
