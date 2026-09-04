"""行动执行中间件(v1.11.2 MS3 W4.1,F3.3)——八步序。

认证(路由层 EDITER)→ permission(scoped token 精确匹配;无声明走
EDITOR 地板)→ 目标解析(共享取数管线,ACL 同路)→ 幂等查重(owner token
裁决)→ 前置求值(谓词 DSL,上下文 target/assess/actor)→ 效果分派
(update_lifecycle=storage 原生行级 update,D1-①/notify=user_state/none)
→ 审计(sys_audit_trail,带 scenario/step/研判依据)→ post_event(进程内
pub/sub,异常隔离)。

⚠️ 步骤序的工程化决策:幂等④在前置⑤**之前**——自失效型行动(效果改掉
前置依赖的状态,如 update_lifecycle 后 state≠pending)的重放必须兑现
「200 已生效不重复写」的幂等承诺,故判重(只需目标上下文渲染键)先于
前置;已获幂等槽后前置不满足 → 槽置 failed(可重认领),不阻塞未来合法
执行。

失败分派(on_failure):REJECT→422;MANUAL/DEAD_LETTER→200+人工/死信
标记(失败审计已落账,幂等键置 failed 可重认领)。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from arrow_lake.actions.events import ActionEvent, publish
from arrow_lake.actions.predicates import ParsedPredicateError, compile_predicate, resolve_path
from arrow_lake.actions.schema import ActionSpec
from arrow_lake.actions.templates import render_payload_item, render_template
from arrow_lake.actions.yaml_io import ActionYamlError, parse_action_yaml
from arrow_lake.semantic.objectset import ObjectSetRows, fetch_object_rows

logger = logging.getLogger(__name__)

__all__ = ["ActionError", "execute_action"]

_EFFECT_TIMEOUT = 60


class ActionError(Exception):
    """受控失败(4xx 语义);exception_class 走 M6 四分类。"""

    def __init__(self, status_code: int, reason: str, *, exception_class: str = "business") -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason
        self.exception_class = exception_class


def _sql_lit(value: Any) -> str:
    """WHERE 子句字面量:单引号包裹,内部单引号翻倍(lancedb update 的
    where 是 SQL 文本;values 走参数化,无需转义)。"""
    return "'" + str(value).replace("'", "''") + "'"


# 裸标识符安全形态(lancedb update 的 where 引用形态约束,W4.5 L-6)
_BARE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


async def execute_action(
    *,
    lake: Any,
    checker: Any,
    user: Any,
    action_id: str,
    dataset: str,
    object_type: str,
    object_id: str,
    reason: str | None,
    scenario_id: str | None = None,
    step_id: str | None = None,
    assess: dict[str, Any] | None = None,  # 回显用(W4.5 H-3:不再信任)
    action_store: Any,
    idempotency_store: Any,
    contract_store: Any,
    alignment_store: Any | None,
    user_state_store: Any,
    rules_store: Any | None = None,
    scenario_store: Any | None = None,
    deny_table_read: Callable[[str, str | None], None],
    acl_enforce: Callable[[str, str], str],
    deny_table_write: Callable[[str, str | None], None] | None = None,
) -> dict[str, Any]:
    """执行一个行动(八步序);返回响应体(200 各态)或抛 ActionError。"""
    from arrow_lake.api.utils import run_sync

    # -- 载入目录条目 -----------------------------------------------------
    rec = action_store.get_version(action_id)
    if rec is None:
        raise ActionError(404, f"action '{action_id}' not in catalog")
    try:
        spec = parse_action_yaml(rec["action_yaml"])
    except ActionYamlError as exc:
        raise ActionError(
            422, f"catalog entry unparseable: {exc}", exception_class="technical"
        ) from exc
    if spec.target.dataset != dataset:
        raise ActionError(
            422,
            f"action targets dataset {spec.target.dataset!r}, not {dataset!r}",
        )

    # -- ② permission(scoped token 精确匹配;空声明=EDITOR 地板已满足) --
    if spec.permission is not None:
        perms = list(getattr(user, "permissions", None) or [])
        if perms and spec.permission not in perms:
            await _audit_denied(lake, user, action_id, dataset, object_type,
                                object_id, f"permission: {spec.permission}")
            raise ActionError(403, f"Missing permission: {spec.permission}")

    if spec.audit.reason_required and not (reason or "").strip():
        raise ActionError(422, "reason is required for this action")

    # -- ③ 目标解析:共享取数管线(读权/表级 deny/行过滤/列 ACL 同路) --
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
        deny_table_read=deny_table_read,
        acl_enforce=acl_enforce,
    )
    if not res.rows:
        raise ActionError(404, f"object '{object_id}' not found in {dataset}.{object_type}")
    if len(res.rows) > 1:
        # W4.5 M-1:标识列值重复 → 写路径歧义(裸标识 where 会命中全部重复
        # 行,可能越过行级 ACL 的可见域)——拒绝执行,正中 limit=2 设计意图
        raise ActionError(
            422,
            f"object '{object_id}' resolves to {len(res.rows)} rows — "
            f"identifier not unique, refusing to execute",
        )
    section = res.contract.tables[object_type]
    if spec.target.object_class not in (section.object_class, object_type):
        raise ActionError(
            422,
            f"action targets object_class {spec.target.object_class!r}, not "
            f"{section.object_class or object_type!r}",
        )

    # -- 写向门禁(W4.5 H-1):物理写必须过 write 检查,与既有写端点同强 --
    # dataset 级 write(role grants/DatasetACL/deny)+ 表级写 deny 双查
    # (closure 绑定 _deny_table_override(write=True),ADMIN 豁免其内)。
    table_param = res.target.split(".", 1)[1] if res.target.startswith(f"{dataset}.") else None
    if spec.effect.type == "update_lifecycle":
        if not checker.check_dataset_access(
            role=user.role,
            dataset=dataset,
            action="write",
            permissions=getattr(user, "permissions", None),
        ):
            await _audit_denied(lake, user, action_id, dataset, object_type,
                                object_id, "dataset write denied")
            raise ActionError(403, f"Write access to dataset '{dataset}' denied")
        if deny_table_write is not None:
            deny_table_write(dataset, table_param)

    # -- 场景归属校验(W4.5 L-1):scenario/step 必须在场景库中真实存在 --
    if scenario_id is not None and scenario_store is not None:
        srec = scenario_store.get_version(scenario_id)
        if srec is None:
            raise ActionError(422, f"scenario '{scenario_id}' not found")
        if step_id is not None:
            from arrow_lake.actions.yaml_io import parse_scenario_yaml

            try:
                scenario = parse_scenario_yaml(srec["scenario_yaml"])
            except Exception:
                raise ActionError(
                    422,
                    f"scenario '{scenario_id}' unparseable; step attribution unverifiable",
                    exception_class="technical",
                ) from None
            if step_id not in {s.id for s in scenario.steps}:
                raise ActionError(
                    422,
                    f"step '{step_id}' not in scenario '{scenario_id}'",
                )
    elif step_id is not None and scenario_id is None:
        raise ActionError(422, "step_id requires scenario_id")

    target_ctx: dict[str, Any] = dict(res.rows[0])
    if res.lifecycle_col is not None and res.lifecycle_col in res.rows[0]:
        target_ctx["lifecycle_state"] = res.rows[0][res.lifecycle_col]
    target_ctx["object_id"] = object_id

    # -- 服务端重评 assess(W4.5 H-3):canonical 字段(confidence/matched_
    # rules/rule_ids/unruly_count)由本进程对 active 规则重算——客户端
    # 提供的 assess 不再进入前置/幂等键/审计依据等任何信任面(防伪造
    # 研判守卫与依据)。前置若引用 canonical 四字段之外的自造键,缺失
    # path → 恒 False(fail-safe)。
    assess_ctx: dict[str, Any] = {
        "confidence": 1.0,
        "matched_rules": 0,
        "rule_ids": [],
        "unruly_count": 0,
    }
    if rules_store is not None:
        from arrow_lake.decisions.assess import (
            compute_confidence,
            evaluate_active_rules,
        )

        conclusions, unruly = await evaluate_active_rules(rules_store, dataset, target_ctx)
        assess_ctx = {
            # W4 #10:真实置信(降权因子;此前恒 1.0)——H-3 服务端重评口径
            "confidence": compute_confidence(
                matched_rules=len(conclusions), unruly_count=len(unruly)
            ),
            "matched_rules": len(conclusions),
            "rule_ids": [c["rule_id"] for c in conclusions],
            "unruly_count": len(unruly),
        }
    actor_ctx: dict[str, Any] = {
        "sub": getattr(user, "sub", ""),
        "role": str(getattr(user.role, "value", user.role)),
        "user_id": getattr(user, "user_id", None),
    }
    ctx: dict[str, Any] = {"target": target_ctx, "assess": assess_ctx, "actor": actor_ctx}

    base = {
        "action_id": action_id,
        "action_version": rec["version"],
        "dataset": dataset,
        "object_type": object_type,
        "object_id": object_id,
        "scenario_id": scenario_id,
        "step_id": step_id,
        "reason": (reason or "").strip() or None,
        "actor": actor_ctx["sub"],
    }

    # -- ④ 幂等查重(owner token 裁决;重放 → 200 已生效不重复执行) -----
    # 序:幂等先于前置——自失效型行动的重放才兑现已生效语义(见模块 docstring)
    idem_key: str | None = None
    if spec.idempotency_key is not None:
        idem_key = render_template(spec.idempotency_key, ctx)
        if not idem_key:
            raise ActionError(
                422,
                "idempotency key rendered empty",
                exception_class="technical",
            )
        owner = uuid4().hex
        acq = await run_sync(
            lambda: idempotency_store.try_acquire(action_id, idem_key, owner=owner),
            timeout=_EFFECT_TIMEOUT,
            label="action_idempotency_acquire",
        )
        if not acq["acquired"]:
            return {
                **base,
                "status": "already_in_effect",
                "idempotency": {"key": idem_key, "state": acq["state"]},
                "effect": None,
                "audit_id": None,
                "event": None,
            }

    # -- ⑤ 前置求值(W4.5 H-2:获槽后任何 422 都置 failed 可重认领, -----
    #    否则槽永久卡 running、该对象对此行动永久谎报 already_in_effect)
    try:
        for p in spec.preconditions:
            try:
                pred = compile_predicate(p)
            except ParsedPredicateError as exc:
                raise ActionError(
                    422,
                    f"precondition unparseable ({p!r}): {exc}",
                    exception_class="technical",
                ) from exc
            if not pred.evaluate(ctx):
                raise ActionError(422, f"precondition not met: {p}")
    except ActionError as exc:
        if idem_key is not None:
            await _mark(
                idempotency_store, action_id, idem_key, "failed", f"precondition: {exc.reason}"
            )
        raise

    # -- ⑥⑦⑧ 效果 → 审计 → 事件 ------------------------------------------
    try:
        effect_outcome = await _run_effect(
            spec=spec,
            res=res,
            ctx=ctx,
            target_ctx=target_ctx,
            lake=lake,
            user=user,
            user_state_store=user_state_store,
            dataset=dataset,
            object_type=object_type,
            object_id=object_id,
        )
    except ActionError:
        if idem_key is not None:
            await _mark(idempotency_store, action_id, idem_key, "failed", "business refusal")
        raise
    except Exception as exc:
        return await _dispatch_failure(
            spec=spec,
            exc=exc,
            idem_key=idem_key,
            base=base,
            idempotency_store=idempotency_store,
            lake=lake,
            user=user,
        )

    audit_payload = {**base, "effect": effect_outcome}
    if spec.audit.include:
        audit_payload["included"] = {
            path: resolve_path(ctx, tuple(path.split("."))) for path in spec.audit.include
        }
    # W4.5 M-3:审计失败不吞效果事实——效果已落库,响应仍 executed(带
    # audit_status=failed 警示)+ best-effort 补记失败标记;幂等置
    # completed(效果真值以存储为准;置 failed 会诱发重放双写)。
    audit_id: str | None = None
    audit_status = "ok"
    try:
        audit_id = await run_sync(
            lambda: lake.audit_record(
                "action.execute",
                dataset_name=dataset,
                actor=actor_ctx["sub"],
                payload=audit_payload,
            ),
            timeout=_EFFECT_TIMEOUT,
            label="action_audit_record",
        )
    except Exception:
        logger.exception("action_audit_record_failed", extra={"action": action_id})
        audit_status = "failed"
        try:
            await run_sync(
                lambda: lake.audit_record(
                    "action.audit_failed",
                    dataset_name=dataset,
                    actor=actor_ctx["sub"],
                    payload={**audit_payload, "audit_error": "record-failed"},
                ),
                timeout=_EFFECT_TIMEOUT,
                label="action_audit_failure_mark",
            )
        except Exception:
            logger.exception("action_audit_failure_mark_failed", extra={"action": action_id})
    if idem_key is not None:
        await _mark(idempotency_store, action_id, idem_key, "completed", None)

    event_info: dict[str, Any] | None = None
    if spec.post_event is not None:
        event = ActionEvent(
            name=spec.post_event.name,
            action_id=action_id,
            dataset=dataset,
            object_id=object_id,
            payload={item: render_payload_item(item, ctx) for item in spec.post_event.payload},
        )
        delivered = publish(event)
        event_info = {"name": event.name, "delivered_to": len(delivered)}

    return {
        **base,
        "status": "executed",
        "effect": effect_outcome,
        "audit_id": audit_id,
        "audit_status": audit_status,
        "assess_recomputed": assess_ctx,
        "event": event_info,
        "idempotency": ({"key": idem_key, "state": "completed"} if idem_key is not None else None),
    }


# --------------------------------------------------------------------------- #
# 效果分派(W4.2)                                                            #
# --------------------------------------------------------------------------- #


async def _run_effect(
    *,
    spec: ActionSpec,
    res: ObjectSetRows,
    ctx: dict[str, Any],
    target_ctx: dict[str, Any],
    lake: Any,
    user: Any,
    user_state_store: Any,
    dataset: str,
    object_type: str,
    object_id: str,
) -> dict[str, Any]:
    from arrow_lake.api.utils import run_sync

    if spec.effect.type == "none":
        return {"type": "none"}

    if spec.effect.type == "notify":
        template = spec.effect.fields.get("message")
        message = (
            render_template(template, ctx)
            if template
            else f"行动 {spec.action_id} 已执行(对象 {object_id})"
        )
        user_id = getattr(user, "user_id", None)
        if user_id is None or user_state_store is None:
            logger.warning("action_notify_skipped_no_user", extra={"action": spec.action_id})
            return {"type": "notify", "delivered": False}
        await run_sync(
            lambda: user_state_store.notify(int(user_id), message, kind="action"),
            timeout=_EFFECT_TIMEOUT,
            label="action_notify",
        )
        return {"type": "notify", "delivered": True, "user_id": user_id}

    # update_lifecycle:storage 原生行级 update(D1-①,容器表经 table= 寻址)
    assert spec.effect.type == "update_lifecycle"  # 封闭集 narrowed by Literal
    lifecycle_col = res.lifecycle_col
    if lifecycle_col is None:
        raise ActionError(
            422,
            f"object_type '{object_type}' declares no lifecycle column — "
            f"update_lifecycle inapplicable",
        )
    # W4.5 L-6:ident_col 须为裸标识符安全形态(lancedb update 的 where
    # 双引号=字符串字面量,无法引用;中文/连字符/关键字列 → 引导换名)
    if not _BARE_IDENT_RE.match(res.ident_col or ""):
        raise ActionError(
            422,
            f"identifier column {res.ident_col!r} is not a bare-identifier "
            f"name — update path cannot address it (rename via alignment)",
        )
    to_state = render_template(spec.effect.to_state or "", ctx)
    # W4.5 M-4:to_state 必须落在契约 lifecycle 词表内且非空(声明式状态
    # 机不失守;模板缺失渲染空串同样拒)
    lc_decl = res.contract.tables[object_type].lifecycle
    if lc_decl is not None and (not to_state or to_state not in lc_decl.states):
        raise ActionError(
            422,
            f"to_state {to_state!r} not in contract lifecycle states {list(lc_decl.states)}",
        )
    # W4.5 L-5:fields 键撞 lifecycle 列 → 效果语义静默漂移,显式拒
    if lifecycle_col in spec.effect.fields:
        raise ActionError(
            422,
            f"effect.fields key {lifecycle_col!r} collides with the lifecycle "
            f"column (to_state already sets it)",
        )
    values: dict[str, str] = {lifecycle_col: to_state}  # 字面量,参数化无注入面
    for col, template in spec.effect.fields.items():
        values[col] = render_template(template, ctx)

    # 物理寻址沿用取数管线的判定:容器二段名 → table= 寻址
    table_param = res.target.split(".", 1)[1] if res.target.startswith(f"{dataset}.") else None
    where = f"{res.ident_col} = {_sql_lit(object_id)}"
    storage = lake._get_storage()

    # H8(四维 review):写前行数核验——fetch 走 ACL 过滤只能看到可见域,
    # 裸标识 WHERE 物理可命中跨分区重复标识的**不可见行**(契约只验
    # pattern 不验唯一性)。count_rows 不过滤:≠1 即拒绝(2=重复标识
    # 越权面;0=行已删),幂等分支在 M-1 查重前早已返回。
    def _count_target() -> int:
        try:
            ds = storage.open_dataset(dataset, table=table_param)
            return int(ds.count_rows(where))
        except Exception as exc:
            raise ActionError(
                422,
                f"cannot verify target uniqueness for '{object_id}': {exc}",
                exception_class="technical",
            ) from exc

    from arrow_lake.api.utils import olap_executor as _olap_exec

    matched = await run_sync(
        _count_target, timeout=_EFFECT_TIMEOUT,
        label="action_count_target", executor=_olap_exec)
    if matched != 1:
        raise ActionError(
            422,
            f"identifier '{object_id}' physically matches {matched} rows "
            f"(cross-partition duplicate identifiers?) — refusing write "
            f"outside the ACL-visible window",
            exception_class="business",
        )

    def _update() -> None:
        storage.update_rows(dataset, where, values, table=table_param)

    # W4.5 M-2:写走 olap 专用池(不占事件循环默认池,沿治本 C 纪律)
    from arrow_lake.api.utils import olap_executor

    await run_sync(
        _update,
        timeout=_EFFECT_TIMEOUT,
        label="action_update_lifecycle",
        executor=olap_executor,
    )
    return {
        "type": "update_lifecycle",
        "to_state": to_state,
        "columns": sorted(values),
        "target": res.target,
    }


async def _audit_denied(
    lake: Any, user: Any, action_id: str, dataset: str, object_type: str,
    object_id: str, reason: str,
) -> None:
    """越权拦截落账(D3:403+action 维度走审计管道,不新增 /metrics)。

    best-effort:审计面故障不掩盖 403 本身。
    """
    from arrow_lake.api.utils import run_sync

    try:
        await run_sync(
            lambda: lake.audit_record(
                "action.denied", dataset_name=dataset,
                actor=getattr(user, "sub", ""),
                payload={"action_id": action_id, "object_type": object_type,
                         "object_id": object_id, "denied": reason},
            ),
            timeout=_EFFECT_TIMEOUT, label="action_audit_denied",
        )
    except Exception:  # noqa: BLE001
        logger.exception("action_denied_audit_failed",
                         extra={"action": action_id})


async def _mark(
    idempotency_store: Any, action_id: str, key: str, state: str, detail: str | None
) -> None:
    from arrow_lake.api.utils import run_sync

    try:
        await run_sync(
            lambda: idempotency_store.mark(action_id, key, state, detail=detail),
            timeout=_EFFECT_TIMEOUT,
            label="action_idempotency_mark",
        )
    except Exception:
        logger.exception("action_idempotency_mark_failed", extra={"action": action_id, "key": key})


async def _dispatch_failure(
    *,
    spec: ActionSpec,
    exc: Exception,
    idem_key: str | None,
    base: dict[str, Any],
    idempotency_store: Any,
    lake: Any,
    user: Any,
) -> dict[str, Any]:
    """非预期失败:on_failure 三分派(失败审计先落账,幂等置 failed)。

    W4.5 M-2:**超时类失败强制 manual_intervention**——run_sync 超时只弃
    结果不杀线程,后台写仍可能落盘;failed 态可自动重认领会诱发双写,
    故超时一律禁自动重放,人工核销(状态以存储为准)。
    """
    from arrow_lake.api.utils import run_sync

    detail = f"{type(exc).__name__}: {exc}"
    exc_class = spec.on_failure.exception_class
    is_timeout = isinstance(exc, TimeoutError)
    if idem_key is not None:
        await _mark(idempotency_store, spec.action_id, idem_key, "failed", detail)
    payload = {
        **base,
        "error": detail,
        "exception_class": exc_class,
        "fallback": spec.on_failure.fallback,
        "timeout": is_timeout,
    }
    try:
        await run_sync(
            lambda: lake.audit_record(
                "action.failed",
                dataset_name=base["dataset"],
                actor=base.get("actor") or "system",
                payload=payload,
            ),
            timeout=_EFFECT_TIMEOUT,
            label="action_audit_failure",
        )
    except Exception:
        logger.exception("action_failure_audit_failed", extra={"action": spec.action_id})

    if spec.on_failure.fallback == "REJECT" and not is_timeout:
        raise ActionError(
            422,
            f"action failed ({exc_class}): {detail}",
            exception_class=exc_class,
        ) from exc
    if is_timeout:
        status = "manual_intervention"  # 禁自动重放(M-2)
    else:
        status = "manual_intervention" if spec.on_failure.fallback == "MANUAL" else "dead_letter"
    return {
        **base,
        "status": status,
        "effect": None,
        "audit_id": None,
        "event": None,
        "error": detail,
        "exception_class": exc_class,
        "idempotency": ({"key": idem_key, "state": "failed"} if idem_key is not None else None),
    }
