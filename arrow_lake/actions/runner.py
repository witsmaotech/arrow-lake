"""场景执行引擎(v1.11.5 W3,§7 scenario runner)。

在 v1.11.2 的「规范+审计词表」(ScenarioSpec,当时注释明说非执行引擎)
之上补执行面。**纯编排器**:spec/实例 store/步执行器全注入——action 步
走 :func:`arrow_lake.actions.middleware.execute_action` 单入口(带
scenario_id/step_id,不改八步),assess 步走
:func:`arrow_lake.decisions.assess.evaluate_active_rules`;router 层组装
闭包后逐步传入。

执行语义(docs_offline/v1115-w3-scenario-runner-design.md §一):
* 可运行 = ``requires ⊆ succeeded`` 且未被网关旁路;步输出写实例上下文
  ``steps.<step_id>``(assess 归一化 canonical 字段;action 为中间件
  返回 dict),根 ``assess`` 镜像最新研判步输出;
* XOR 网关:求值点 = 其引用步(then∪else)的 requires 全部终态;决策后
  落选臂永久 skipped 并级联(依赖 skipped 的步一并 skipped);
* AND 并行(and_split):无运行时门控——可运行步经 ``asyncio.gather``
  单 worker 内并发(声明性文档,不引外部队列);
* 死锁守卫:无可运行步且仍有未终态步 → failed("unsatisfiable remains");
* 超时:场景级 deadline 每批派发前检查;超限 → 未终态步标 timeout、执行
  on_timeout 升级步、实例终态 timeout;
* 补偿(声明式+人工,policy 仅 manual):action 步落入 failed/dead_letter/
  manual_intervention(效果归属均不确定)且 ActionSpec 带 compensation →
  **不自动执行**;实例终态 compensated,步行与实例行记 pending_compensation
  (console 复用单 action execute 端点逐条人工执行);
* 失败传播:步失败(REJECT)→ 主线停;manual_intervention/dead_letter
  (效果可能部分落盘)→ 实例 failed,步态原样保留;
* 断点续跑:``run(resume=True)`` 重入运行循环——已 succeeded 的 action
  步不重跑(重放经幂等 ``already_in_effect`` 视作 succeeded 由中间件兑
  现),assess 步纯求值重跑,网关决策重算(上一轮 skipped 步保持终态);
* terminate:运行中实例被外部终止 → 下一轮循环退出,不再派发新步。

store 写走 run_sync(libSQL 远端 HTTP 不阻塞事件循环,沿 W4.5 L-2);
多 worker 语义:实例行是 SoT,runner 只存在于持有 task 的进程(登记
限制,不在本版解决)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from arrow_lake.actions.predicates import ParsedPredicateError, compile_predicate
from arrow_lake.actions.schema import ActionSpec, ScenarioSpec, ScenarioStep

logger = logging.getLogger(__name__)

__all__ = ["ScenarioRunner", "parse_iso_duration"]

# 步终态(succeeded/skipped 之外均不可自动推进下游)
_STEP_TERMINAL = frozenset(
    {"succeeded", "failed", "skipped", "manual_intervention", "dead_letter", "timeout"}
)
# 中间件 execute_action 返回 status → 步态
_ACTION_STATUS_MAP = {
    "executed": "succeeded",
    "already_in_effect": "succeeded",  # 幂等重放(断点续跑的正道)
    "manual_intervention": "manual_intervention",
    "dead_letter": "dead_letter",
}

_DEFAULT_ASSESS: dict[str, Any] = {
    "confidence": 1.0,
    "matched_rules": 0,
    "rule_ids": [],
    "unruly_count": 0,
}

# 与 schema._ISO8601_DURATION_RE 同形态(保存期已校验,这里只做换算)
_DURATION_RE = re.compile(
    r"^P(?!$)(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?"
    r"(?:T(?=.)(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$"
)


def parse_iso_duration(text: str) -> float:
    """ISO-8601 duration(PT30M)→ 秒;不可解析 → ValueError(零依赖手写)。"""
    m = _DURATION_RE.match(text)
    if m is None:
        raise ValueError(f"not an ISO-8601 duration: {text!r}")
    years, months, weeks, days, hours, minutes, seconds = m.groups()
    total = (
        int(years or 0) * 365 * 86400
        + int(months or 0) * 30 * 86400
        + int(weeks or 0) * 7 * 86400
        + int(days or 0) * 86400
        + int(hours or 0) * 3600
        + int(minutes or 0) * 60
        + float(seconds or 0)
    )
    return float(total)


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_deadline(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sync(func: Any, *args: Any, **kwargs: Any) -> Awaitable[Any]:
    """store 写下线程(事件循环安全;纯逻辑测试同样可用)。"""
    from arrow_lake.api.utils import run_sync

    return run_sync(lambda: func(*args, **kwargs), timeout=30, label="scenario_store_write")


def _normalize_assess(out: Mapping[str, Any]) -> dict[str, Any]:
    """assess 步输出归一化:canonical 字段补全(evaluate_active_rules 只给
    conclusions/unruly,conf/matched/rule_ids 在此派生,与中间件同口径)。"""
    conclusions = list(out.get("conclusions") or [])
    unruly = list(out.get("unruly") or [])
    return {
        "conclusions": conclusions,
        "unruly": unruly,
        "confidence": out.get("confidence", 1.0),
        "matched_rules": out.get("matched_rules", len(conclusions)),
        "rule_ids": list(
            out.get("rule_ids") or [c.get("rule_id") for c in conclusions if c.get("rule_id")]
        ),
        "unruly_count": out.get("unruly_count", len(unruly)),
    }


class ScenarioRunner:
    """单场景实例的进程内运行循环(实例行是 SoT;``run(resume=True)`` 重入)。"""

    def __init__(
        self,
        *,
        spec: ScenarioSpec,
        store: Any,  # ScenarioInstanceStore-like
        instance_id: int,
        run_action: Callable[[str, str], Awaitable[dict[str, Any]]],
        run_assess: Callable[[str | None], Awaitable[dict[str, Any]]],
        action_specs: Mapping[str, ActionSpec] | None = None,
    ) -> None:
        self._spec = spec
        self._store = store
        self._instance_id = instance_id
        self._run_action = run_action
        self._run_assess = run_assess
        self._action_specs = dict(action_specs or {})
        self._kind_of = {
            s.id: ("assess" if s.type == "assess" else "action") for s in self._spec.steps
        }

    # ------------------------------------------------------------------ #
    # 运行循环                                                             #
    # ------------------------------------------------------------------ #

    async def run(self, *, resume: bool = False) -> None:
        inst = await _sync(self._store.get_instance, self._instance_id)
        if inst is None or inst.get("status") != "running":
            return  # 不存在/已终态/已被终止
        ctx: dict[str, Any] = json.loads(inst.get("context_json") or "{}")
        ctx.setdefault("target", {})
        ctx.setdefault("actor", {})
        ctx.setdefault("assess", dict(_DEFAULT_ASSESS))
        steps_out: dict[str, Any] = ctx.setdefault("steps", {})
        status: dict[str, str] = {
            r["step_id"]: r["status"]
            for r in await _sync(self._store.list_step_runs, self._instance_id)
        }
        if resume:
            # assess 步纯求值无副作用 → 重跑(重入时撤销其 succeeded 记忆,
            # 行由 start/finish upsert 覆盖;action 步的 succeeded 保持——
            # 重放语义由中间件幂等键兑现)
            for step in self._spec.steps:
                if step.type == "assess" and status.get(step.id) == "succeeded":
                    del status[step.id]
        deadline = _parse_deadline(inst.get("deadline_at"))
        decided: dict[str, bool] = {}

        while True:
            inst = await _sync(self._store.get_instance, self._instance_id)
            if inst is None or inst.get("status") != "running":
                return  # 外部 terminate

            succeeded = {sid for sid, st in status.items() if st == "succeeded"}
            terminal = {sid for sid, st in status.items() if st in _STEP_TERMINAL}

            # -- XOR 网关决策:引用步 requires 全终态后求值一次 ------------------
            for gw in self._spec.gateways:
                if gw.type != "xor" or gw.id in decided:
                    continue
                reqs = self._gateway_requirements(gw)
                if reqs and not reqs.issubset(terminal):
                    continue  # 求值点未到
                try:
                    decided[gw.id] = compile_predicate(gw.when or "true").evaluate(ctx)
                except ParsedPredicateError:
                    decided[gw.id] = False
                losing = set(gw.else_) if decided[gw.id] else set(gw.then)
                for sid in sorted(losing):
                    if status.get(sid) not in _STEP_TERMINAL:
                        await self._mark_step(sid, "skipped")
                        status[sid] = "skipped"
                        terminal.add(sid)
            # 级联:依赖 skipped 的步一并 skipped(替代路径已定,主线依赖作废)
            changed = True
            while changed:
                changed = False
                for step in self._spec.steps:
                    if status.get(step.id) in _STEP_TERMINAL:
                        continue
                    if any(status.get(req) == "skipped" for req in step.requires):
                        await self._mark_step(step.id, "skipped")
                        status[step.id] = "skipped"
                        changed = True

            # -- 超时检查(每批派发前)------------------------------------------
            if deadline is not None and _now() >= deadline:
                await self._handle_timeout(status)
                return

            # -- 可运行集 --------------------------------------------------------
            runnable = self._runnable_steps(status, succeeded, decided)
            if not runnable:
                pending = [
                    s.id for s in self._spec.steps if status.get(s.id) not in _STEP_TERMINAL
                ]
                if pending:
                    await self._finish(
                        ctx, "failed", error="unsatisfiable remains: " + ", ".join(sorted(pending))
                    )
                else:
                    await self._finish(ctx, "completed")
                return

            # -- 派发(gather 并发;实例行写在 loop 上串行,无锁)-----------------
            await _sync(
                self._store.update_instance,
                self._instance_id,
                current_step=",".join(s.id for s in runnable),
                context_json=json.dumps(ctx, ensure_ascii=False, default=str),
            )
            results = await asyncio.gather(
                *(self._exec_step(step, ctx, steps_out) for step in runnable),
                return_exceptions=True,
            )
            stop: tuple[str, str, list[str] | None] | None = None
            for step, res in zip(runnable, results):
                if isinstance(res, BaseException):  # 防御:_exec_step 已内捕
                    outcome = (step.id, "failed", None, f"{type(res).__name__}: {res}")
                else:
                    outcome = res
                status[outcome[0]] = outcome[1]
                if outcome[1] != "succeeded":
                    stop = (outcome[0], outcome[1], outcome[2])
            if stop is not None:
                await self._handle_failure(stop, ctx)
                return

    # ------------------------------------------------------------------ #
    # 内部件                                                               #
    # ------------------------------------------------------------------ #

    def _gateway_requirements(self, gw: Any) -> set[str]:
        reqs: set[str] = set()
        for sid in set(gw.then) | set(gw.else_):
            step = next((s for s in self._spec.steps if s.id == sid), None)
            reqs.update(step.requires if step else ())
        return reqs

    def _runnable_steps(
        self,
        status: dict[str, str],
        succeeded: set[str],
        decided: dict[str, bool],
    ) -> list[ScenarioStep]:
        out: list[ScenarioStep] = []
        for step in self._spec.steps:
            if status.get(step.id) in ("succeeded", "skipped"):
                continue
            if not set(step.requires).issubset(succeeded):
                continue
            # 网关旁路:XOR 引用的步须落在已决策网关的选中臂
            blocked = False
            for gw in self._spec.gateways:
                if gw.type != "xor" or step.id not in (set(gw.then) | set(gw.else_)):
                    continue
                if gw.id not in decided:
                    blocked = True  # 求值点未到,暂不可运行
                    break
                chosen = set(gw.then) if decided[gw.id] else set(gw.else_)
                if step.id not in chosen:
                    blocked = True  # 落选臂(级联 skipped 已兜底,防御)
                    break
            if not blocked:
                out.append(step)
        return out

    async def _mark_step(self, step_id: str, status: str, *, error: str | None = None) -> None:
        """步行 upsert(未启动的 skipped/timeout 步也要有行,UI 时间线完整)。"""
        await _sync(
            self._store.finish_step,
            self._instance_id,
            step_id,
            self._kind_of.get(step_id, "action"),
            status,
            error=error,
        )

    async def _exec_step(
        self,
        step: ScenarioStep,
        ctx: dict[str, Any],
        steps_out: dict[str, Any],
    ) -> tuple[str, str, list[str] | None, None]:
        """执行单步并落步行;返回 (step_id, 步态, 补偿待办|None, 未用)。"""
        kind = self._kind_of[step.id]
        await _sync(self._store.start_step, self._instance_id, step.id, kind)
        pending_comp: list[str] | None = None
        try:
            if kind == "assess":
                out: dict[str, Any] = _normalize_assess(await self._run_assess(step.rules_scope))
                step_status = "succeeded"
                ctx["assess"] = dict(out)  # 根 assess 镜像最新研判步输出
            else:
                out = dict(await self._run_action(step.action or "", step.id))
                step_status = _ACTION_STATUS_MAP.get(str(out.get("status")), "failed")
        except Exception as exc:  # noqa: BLE001 — 步失败即编排事实,不上抛
            out = {"error": f"{type(exc).__name__}: {exc}"}
            step_status = "failed"
        if step_status in ("failed", "manual_intervention", "dead_letter"):
            # 三态的效果归属均不确定(REJECT 异常/dead_letter/超时)——凡有
            # compensation 声明即挂人工待办;纯业务拒绝挂了也无害(人工裁决)
            spec = self._action_specs.get(step.action or "")
            if spec is not None and spec.compensation is not None:
                pending_comp = [spec.compensation.action]
                out["pending_compensation"] = pending_comp
        steps_out[step.id] = out
        await _sync(
            self._store.finish_step,
            self._instance_id,
            step.id,
            kind,
            step_status,
            output_json=json.dumps(out, ensure_ascii=False, default=str),
        )
        return step.id, step_status, pending_comp, None

    async def _handle_failure(
        self, stop: tuple[str, str, list[str] | None], ctx: dict[str, Any]
    ) -> None:
        """步失败/manual/死信 → 主线停;补偿声明 → compensated+待办清单。"""
        step_id, step_status, pending_comp = stop
        if pending_comp:
            await self._finish(ctx, "compensated", pending_compensation=pending_comp)
            return
        await self._finish(ctx, "failed", error=f"step '{step_id}' {step_status}")

    async def _handle_timeout(self, status: dict[str, str]) -> None:
        """deadline 已过:未终态步标 timeout → on_timeout 升级步 → 实例 timeout。"""
        for step in self._spec.steps:
            if status.get(step.id) not in _STEP_TERMINAL:
                await self._mark_step(step.id, "timeout", error="scenario deadline exceeded")
                status[step.id] = "timeout"
        if self._spec.on_timeout is not None:
            esc = next((s for s in self._spec.steps if s.id == self._spec.on_timeout), None)
            if esc is not None and status.get(esc.id) != "succeeded":
                ctx: dict[str, Any] = {}  # 升级步输出只进步行(实例行即将终态)
                try:
                    await self._exec_step(esc, ctx, ctx.setdefault("steps", {}))
                except Exception:  # noqa: BLE001 — 升级失败不掩盖 timeout 终态
                    logger.exception(
                        "scenario_escalation_failed", extra={"instance": self._instance_id}
                    )
        await self._finish({}, "timeout", error="scenario deadline exceeded")

    async def _finish(
        self,
        ctx: dict[str, Any],
        status: str,
        *,
        error: str | None = None,
        pending_compensation: list[str] | None = None,
    ) -> None:
        await _sync(
            self._store.update_instance,
            self._instance_id,
            status=status,
            error=error,
            pending_compensation=pending_compensation,
            context_json=(
                json.dumps(ctx, ensure_ascii=False, default=str) if ctx else None
            ),
            finished=True,
        )
