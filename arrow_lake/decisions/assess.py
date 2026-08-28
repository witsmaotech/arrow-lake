"""研判引擎(v1.11.2 MS3 W3.2,F3.1)——对象 → 规则求值 → 结论+可行动作。

管线:
1. **对象取数走 W3.1 共享管线** ``fetch_object_rows``(读权守卫/契约
   S8/物理寻址/表级 deny/对齐/ACL/执行全同 objects 端点——S6:研判输入
   =对齐后口径,不建旁路),``object_id`` 经标识列 eq 过滤取单对象;
2. 规则求值:scope=数据集 + ``*`` 的 active 规则,``condition_expr`` 按
   谓词 DSL 编译(lru 缓存,与 W1 同源);**编译失败 → unruly 列表,
   不炸整个研判**(S8,fail-open 到条);求值上下文 = ``target``(对齐后
   属性 + lifecycle_state + object_id,缺失比较恒 False);
3. confidence 恒 1.0(确定性规则,S10;LLM 置信度留 F3.2);
4. actionable:行动目录反查(dataset+object_class 匹配本对象类)且
   preconditions 对 {target, assess, actor} 求值为真的动作。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from arrow_lake.actions.predicates import ParsedPredicateError, compile_predicate
from arrow_lake.actions.yaml_io import ActionYamlError, parse_action_yaml
from arrow_lake.semantic.objectset import fetch_object_rows

logger = logging.getLogger(__name__)

__all__ = ["assess_object", "evaluate_active_rules", "parse_catalog_action"]


# ---------------------------------------------------------------------------
# 共享子程序(W4.5 review:H-3 服务端重评 + L-2 事件循环安全)
# ---------------------------------------------------------------------------

# 目录条目解析缓存:action_id → (source_hash, spec)。写入整表替换保序,
# 256 帽(管理面人工维护的目录规模远低于此;哈希变 → 重解析)。
_SPEC_CACHE: dict[str, tuple[str, Any]] = {}
_SPEC_CACHE_MAX = 256


def parse_catalog_action(rec: dict[str, Any]) -> Any | None:
    """带缓存的目录条目解析(腐烂条目 → None,调用方跳过)。"""
    aid = rec.get("scope") or ""
    cached = _SPEC_CACHE.get(aid)
    if cached is not None and cached[0] == rec.get("source_hash"):
        return cached[1]
    try:
        spec = parse_action_yaml(rec["action_yaml"])
    except ActionYamlError:
        logger.warning("assess_catalog_entry_unparseable", extra={"action": aid})
        return None
    if len(_SPEC_CACHE) >= _SPEC_CACHE_MAX:
        _SPEC_CACHE.clear()
    _SPEC_CACHE[aid] = (rec.get("source_hash") or "", spec)
    return spec


async def evaluate_active_rules(
    rules_store: Any,
    dataset: str,
    target_ctx: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """active 规则(scope=数据集+``*``)对 target 上下文求值。

    返回 (conclusions, unruly);unruly fail-open 到条(S8)。libSQL 调用
    经 run_sync 下线程(W4.5 L-2:不阻塞事件循环)。
    """
    from arrow_lake.api.utils import run_sync

    rules = await run_sync(
        lambda: (
            rules_store.list_rules(scope=dataset, status="active")
            + rules_store.list_rules(scope="*", status="active")
        ),
        timeout=30,
        label="assess_list_rules",
    )
    ctx = {"target": target_ctx}
    conclusions: list[dict[str, Any]] = []
    unruly: list[str] = []
    for r in rules:
        try:
            pred = compile_predicate(r["condition_expr"])
        except ParsedPredicateError:
            unruly.append(r["rule_id"])
            continue
        if pred.evaluate(ctx):
            conclusions.append(
                {
                    "rule_id": r["rule_id"],
                    "rule_type": r.get("rule_type"),
                    "version": r.get("version"),
                    "conclusion": r["conclusion"],
                }
            )
    return conclusions, unruly


async def assess_object(
    *,
    lake: Any,
    checker: Any,
    role: Any,
    permissions: Any,
    actor_sub: str,
    dataset: str,
    object_type: str,
    object_id: str,
    contract_store: Any,
    alignment_store: Any | None,
    rules_store: Any,
    action_store: Any | None,
    deny_table_read: Callable[[str, str | None], None],
    acl_enforce: Callable[[str, str], str],
) -> dict[str, Any]:
    """研判一个对象:取数 → 规则求值 → 结论/可行动作。

    错误语义与 objects 端点同源:无契约 422(S8)、读权/表级 deny 403、
    对象不存在 404。
    """
    res = await fetch_object_rows(
        lake=lake,
        checker=checker,
        role=role,
        permissions=permissions,
        dataset=dataset,
        object_type=object_type,
        object_id=object_id,
        limit=2,  # 1 行即目标;2 用于暴露标识列不唯一的畸形数据
        contract_store=contract_store,
        alignment_store=alignment_store,
        deny_table_read=deny_table_read,
        acl_enforce=acl_enforce,
    )
    if not res.rows:
        raise HTTPException(
            status_code=404,
            detail=f"object '{object_id}' not found in {dataset}.{object_type}",
        )
    if len(res.rows) > 1:
        # W4.5 M-1:标识列值重复(limit=2 暴露畸形数据的设计意图)。研判只读
        # → 取首行但显式告警;写侧(middleware)对同形态直接 422。
        logger.warning(
            "assess_identifier_not_unique",
            extra={
                "dataset": dataset,
                "object_type": object_type,
                "object_id": object_id,
                "rows": len(res.rows),
            },
        )
    row = res.rows[0]

    target_ctx: dict[str, Any] = dict(row)
    if res.lifecycle_col is not None and res.lifecycle_col in row:
        target_ctx["lifecycle_state"] = row[res.lifecycle_col]
    target_ctx["object_id"] = object_id

    conclusions, unruly = await evaluate_active_rules(rules_store, dataset, target_ctx)

    assess_ctx: dict[str, Any] = {
        "confidence": 1.0,
        "matched_rules": len(conclusions),
        "rule_ids": [c["rule_id"] for c in conclusions],
        "unruly_count": len(unruly),
    }

    from arrow_lake.api.utils import run_sync

    actionable = await run_sync(
        lambda: _actionable_actions(
            action_store=action_store,
            dataset=dataset,
            object_type=object_type,
            section_object_class=(res.contract.tables[object_type].object_class),
            target_ctx=target_ctx,
            assess_ctx=assess_ctx,
            actor_ctx={"sub": actor_sub, "role": str(getattr(role, "value", role))},
        ),
        timeout=30,
        label="assess_actionable",
    )

    return {
        "dataset": dataset,
        "object_type": object_type,
        "object_id": object_id,
        "lifecycle_state": target_ctx.get("lifecycle_state"),
        "conclusions": conclusions,
        "matched_rules": len(conclusions),
        "confidence": 1.0,
        "unruly": unruly,
        "actionable": actionable,
    }


def _actionable_actions(
    *,
    action_store: Any,
    dataset: str,
    object_type: str,
    section_object_class: str | None,
    target_ctx: dict[str, Any],
    assess_ctx: dict[str, Any],
    actor_ctx: dict[str, Any],
) -> list[str]:
    """行动目录反查:dataset+object_class 匹配且 preconditions 全真。

    目录条目腐烂(解析失败/前置不可编译)→ 跳过该条,不阻塞研判。
    """
    if action_store is None:
        return []
    out: list[str] = []
    for scope in action_store.list_scopes():
        rec = action_store.get_version(scope["scope"])
        if rec is None:
            continue
        spec = parse_catalog_action(rec)  # 带缓存;腐烂条目 → None 跳过
        if spec is None:
            continue
        if spec.target.dataset != dataset:
            continue
        if spec.target.object_class not in (section_object_class, object_type):
            continue
        pre_ctx = {"target": target_ctx, "assess": assess_ctx, "actor": actor_ctx}
        try:
            # 无前置 = 恒可行动作(all(空)=True)
            ok = all(compile_predicate(p).evaluate(pre_ctx) for p in spec.preconditions)
        except ParsedPredicateError:
            continue
        if ok:
            out.append(spec.action_id)
    return out
