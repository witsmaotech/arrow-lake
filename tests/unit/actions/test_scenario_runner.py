"""W3(v1.11.5)——场景执行引擎纯逻辑(runner)。

契约(docs_offline/v1115-w3-scenario-runner-design.md §一):
* 顺序 DAG:requires 门槛 + 步输出写 ``steps.<step_id>`` + 全绿 completed;
* XOR 网关:引用步 requires 终态后求值 when,选中臂可跑、另一臂永久
  skipped,级联(依赖 skipped 的步一并 skipped);
* AND 并行(and_split):分支成员步并发(gather 真同时在飞);
* 死锁守卫:无可运行步且仍有未终态步 → failed("unsatisfiable remains");
* 超时:deadline 已过 → 未终态已启步标 timeout、执行 on_timeout 升级步、
  实例终态 timeout;
* 补偿(声明式+人工):action 步失败且 ActionSpec 带 compensation → 实例
  compensated + pending_compensation(步行+实例行),不自动执行;
* 失败传播:步失败(REJECT)无补偿 → 实例 failed,下游不跑;
* 断点续跑:已 succeeded 步不重跑;assess 步重跑;重放的 action 步经
  幂等 already_in_effect 视作 succeeded → 续跑至 completed;
* terminate:运行中实例外部终止 → runner 下一轮退出,不再执行新步。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from arrow_lake.actions.runner import ScenarioRunner
from arrow_lake.actions.schema import (
    ActionEffect,
    ActionSpec,
    ActionTarget,
    Compensation,
    ScenarioGateway,
    ScenarioSpec,
    ScenarioStep,
)

# --------------------------------------------------------------------------- #
# FakeStore:镜像 ScenarioInstanceStore 接口(内存版)                          #
# --------------------------------------------------------------------------- #


class FakeStore:
    def __init__(self) -> None:
        self.instances: dict[int, dict[str, Any]] = {}
        self.step_runs: dict[int, dict[str, dict[str, Any]]] = {}
        self._next = 1

    def create_instance(
        self,
        *,
        scenario_id: str,
        scenario_version: int,
        dataset: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        actor: str = "",
        context_json: str = "{}",
        deadline_at: str | None = None,
    ) -> int:
        iid = self._next
        self._next += 1
        self.instances[iid] = {
            "id": iid,
            "scenario_id": scenario_id,
            "scenario_version": scenario_version,
            "dataset": dataset,
            "object_type": object_type,
            "object_id": object_id,
            "status": "running",
            "current_step": None,
            "context_json": context_json,
            "deadline_at": deadline_at,
            "pending_compensation_json": "[]",
            "error": None,
            "actor": actor,
            "finished_at": None,
        }
        self.step_runs[iid] = {}
        return iid

    def get_instance(self, instance_id: int) -> dict[str, Any] | None:
        return self.instances.get(instance_id)

    def list_instances(
        self,
        *,
        scenario_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        out = []
        for row in sorted(self.instances.values(), key=lambda r: -r["id"]):
            if scenario_id is not None and row["scenario_id"] != scenario_id:
                continue
            if status is not None and row["status"] != status:
                continue
            out.append(row)
        return out[:limit]

    def update_instance(
        self,
        instance_id: int,
        *,
        status: str | None = None,
        current_step: str | None = None,
        context_json: str | None = None,
        error: str | None = None,
        pending_compensation: list[str] | None = None,
        finished: bool = False,
    ) -> bool:
        row = self.instances.get(instance_id)
        if row is None:
            return False
        if status is not None:
            row["status"] = status
        if current_step is not None:
            row["current_step"] = current_step
        if context_json is not None:
            row["context_json"] = context_json
        if error is not None:
            row["error"] = error
        if pending_compensation is not None:
            row["pending_compensation_json"] = json.dumps(pending_compensation)
        if finished:
            row["finished_at"] = "2026-09-04T00:00:00Z"
        return True

    def start_step(self, instance_id: int, step_id: str, kind: str) -> None:
        runs = self.step_runs[instance_id]
        rec = runs.get(step_id)
        if rec is None:
            runs[step_id] = {
                "id": len(runs) + 1,
                "instance_id": instance_id,
                "step_id": step_id,
                "kind": kind,
                "status": "running",
                "output_json": "{}",
                "error": None,
            }
        else:
            rec.update(status="running", error=None, output_json="{}")

    def finish_step(
        self,
        instance_id: int,
        step_id: str,
        kind: str,
        status: str,
        *,
        output_json: str | None = None,
        error: str | None = None,
    ) -> bool:
        # upsert 语义:未启动的 skipped/timeout 步也要有行(UI 时间线完整)
        runs = self.step_runs[instance_id]
        rec = runs.get(step_id)
        if rec is None:
            runs[step_id] = {
                "id": len(runs) + 1,
                "instance_id": instance_id,
                "step_id": step_id,
                "kind": kind,
                "status": status,
                "output_json": output_json or "{}",
                "error": error,
            }
            return True
        rec["status"] = status
        if output_json is not None:
            rec["output_json"] = output_json
        if error is not None:
            rec["error"] = error
        return True

    def list_step_runs(self, instance_id: int) -> list[dict[str, Any]]:
        return sorted(self.step_runs[instance_id].values(), key=lambda r: r["id"])


# --------------------------------------------------------------------------- #
# 构造件                                                                       #
# --------------------------------------------------------------------------- #


def _spec(
    steps: list[ScenarioStep],
    gateways: list[ScenarioGateway] | None = None,
    *,
    timeout: str | None = None,
    on_timeout: str | None = None,
) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id="SCN.TEST",
        title="测试场景",
        steps=tuple(steps),
        gateways=tuple(gateways or ()),
        timeout=timeout,
        on_timeout=on_timeout,
    )


def _action_spec(action_id: str, *, compensation: str | None = None) -> ActionSpec:
    return ActionSpec(
        action_id=action_id,
        title=action_id,
        target=ActionTarget(dataset="gas_net", object_class="告警事件"),
        effect=ActionEffect(type="none"),
        compensation=(
            Compensation(action=compensation) if compensation is not None else None
        ),
    )


def _make_runner(
    store: FakeStore,
    spec: ScenarioSpec,
    instance_id: int,
    *,
    action_impl: dict[str, Any] | None = None,
    assess_impl: dict[str, Any] | None = None,
    action_specs: dict[str, ActionSpec] | None = None,
    calls: list[tuple[str, str]] | None = None,
) -> ScenarioRunner:
    """组装 runner;action_impl/assess_impl 按 step 编排返回值或异常。"""

    async def run_action(action_id: str, step_id: str) -> dict[str, Any]:
        if calls is not None:
            calls.append(("action", step_id))
        impl = (action_impl or {}).get(step_id, {"status": "executed"})
        if isinstance(impl, Exception):
            raise impl
        return dict(impl)

    async def run_assess(rules_scope: str | None) -> dict[str, Any]:
        if calls is not None:
            calls.append(("assess", rules_scope or ""))
        impl = (assess_impl or {}).get(rules_scope or "", {"conclusions": [], "unruly": []})
        if isinstance(impl, Exception):
            raise impl
        return dict(impl)

    return ScenarioRunner(
        spec=spec,
        store=store,
        instance_id=instance_id,
        run_action=run_action,
        run_assess=run_assess,
        action_specs=action_specs or {},
    )


def _seed_instance(store: FakeStore, *, deadline_at: str | None = None) -> int:
    return store.create_instance(
        scenario_id="SCN.TEST",
        scenario_version=1,
        dataset="gas_net",
        object_type="alerts",
        object_id="GAS.ALERT.001",
        actor="op",
        context_json=json.dumps(
            {"target": {"object_id": "GAS.ALERT.001"}, "actor": {"sub": "op"}}
        ),
        deadline_at=deadline_at,
    )


async def _noop_assess(rules_scope: str | None) -> dict[str, Any]:
    return {"conclusions": [], "unruly": []}


# --------------------------------------------------------------------------- #
# 1. 顺序 DAG                                                                  #
# --------------------------------------------------------------------------- #


async def test_sequential_dag_completes_and_feeds_context() -> None:
    store = FakeStore()
    spec = _spec(
        [
            ScenarioStep(id="assess1", type="assess", rules_scope="gas_net"),
            ScenarioStep(id="act_a", action="ACT.A", requires=("assess1",)),
            ScenarioStep(id="act_b", action="ACT.B", requires=("act_a",)),
        ]
    )
    iid = _seed_instance(store)
    calls: list[tuple[str, str]] = []
    runner = _make_runner(
        store,
        spec,
        iid,
        assess_impl={"gas_net": {"conclusions": [{"rule_id": "R1"}], "unruly": []}},
        action_impl={"act_a": {"status": "executed", "effect": {"type": "none"}}},
        calls=calls,
    )
    await runner.run()

    inst = store.get_instance(iid)
    assert inst is not None and inst["status"] == "completed"
    assert inst["finished_at"] is not None
    # 步序:assess → act_a → act_b(requires 门槛保证)
    assert calls == [("assess", "gas_net"), ("action", "act_a"), ("action", "act_b")]
    # 步输出进实例上下文 steps.<step_id>
    ctx = json.loads(inst["context_json"])
    assert ctx["steps"]["assess1"]["matched_rules"] == 1
    assert ctx["steps"]["act_a"]["status"] == "executed"
    # 根 assess 镜像最新研判步输出
    assert ctx["assess"]["matched_rules"] == 1
    runs = {r["step_id"]: r["status"] for r in store.list_step_runs(iid)}
    assert runs == {"assess1": "succeeded", "act_a": "succeeded", "act_b": "succeeded"}


# --------------------------------------------------------------------------- #
# 2. XOR 双臂                                                                   #
# --------------------------------------------------------------------------- #


def _xor_scenario() -> ScenarioSpec:
    return _spec(
        [
            ScenarioStep(id="assess1", type="assess", rules_scope="gas_net"),
            ScenarioStep(id="escalate", action="ACT.ESC", requires=("assess1",), path="substitute"),
            ScenarioStep(id="notify", action="ACT.NOTIFY", requires=("assess1",)),
        ],
        [
            ScenarioGateway(
                id="gw1",
                type="xor",
                when="steps.assess1.confidence < 0.6",
                then=("escalate",),
                else_=("notify",),
            )
        ],
    )


async def test_xor_then_arm_runs_else_skipped() -> None:
    store = FakeStore()
    spec = _xor_scenario()
    iid = _seed_instance(store)
    calls: list[tuple[str, str]] = []
    runner = _make_runner(
        store, spec, iid,
        assess_impl={"gas_net": {"conclusions": [], "unruly": [], "confidence": 0.3}},
        calls=calls,
    )
    await runner.run()

    inst = store.get_instance(iid)
    assert inst is not None and inst["status"] == "completed"
    runs = {r["step_id"]: r["status"] for r in store.list_step_runs(iid)}
    assert runs == {"assess1": "succeeded", "escalate": "succeeded", "notify": "skipped"}
    assert ("action", "escalate") in calls
    assert ("action", "notify") not in calls


async def test_xor_else_arm_runs_then_skipped() -> None:
    store = FakeStore()
    spec = _xor_scenario()
    iid = _seed_instance(store)
    runner = _make_runner(
        store, spec, iid,
        assess_impl={"gas_net": {"conclusions": [], "unruly": [], "confidence": 0.95}},
    )
    await runner.run()

    runs = {r["step_id"]: r["status"] for r in store.list_step_runs(iid)}
    assert runs == {"assess1": "succeeded", "escalate": "skipped", "notify": "succeeded"}
    assert store.get_instance(iid)["status"] == "completed"


async def test_xor_substitute_arm_downweights_confidence() -> None:
    """W4 #10 网关评估参与:走 substitute(else)臂 → 根 assess 置信 ×0.9。"""
    store = FakeStore()
    spec = _xor_scenario()
    iid = _seed_instance(store)
    runner = _make_runner(
        store, spec, iid,
        assess_impl={"gas_net": {"conclusions": [], "unruly": [], "confidence": 0.8}},
    )
    await runner.run()

    ctx = json.loads(store.get_instance(iid)["context_json"])
    assert ctx["assess"]["confidence"] == 0.72  # 0.8 × 0.9
    assert ctx["assess"]["gateway_downweight"] == ["gw1"]


async def test_xor_skip_cascades_to_dependents() -> None:
    store = FakeStore()
    spec = _spec(
        [
            ScenarioStep(id="assess1", type="assess", rules_scope="gas_net"),
            ScenarioStep(id="notify", action="ACT.NOTIFY", requires=("assess1",)),
            ScenarioStep(id="log", action="ACT.LOG", requires=("assess1",)),
            ScenarioStep(id="close", action="ACT.CLOSE", requires=("notify",)),
        ],
        [
            ScenarioGateway(
                id="gw1", type="xor", when="steps.assess1.confidence >= 0.9",
                then=("notify",), else_=("log",),
            )
        ],
    )
    iid = _seed_instance(store)
    calls: list[tuple[str, str]] = []
    runner = _make_runner(
        store, spec, iid, calls=calls,
        assess_impl={"gas_net": {"conclusions": [], "unruly": [], "confidence": 0.3}},
    )
    await runner.run()

    # when 假 → then 臂(notify)skipped;依赖 notify 的 close 级联 skipped;
    # else 臂(log)正常运行
    runs = {r["step_id"]: r["status"] for r in store.list_step_runs(iid)}
    assert runs == {
        "assess1": "succeeded", "notify": "skipped", "close": "skipped", "log": "succeeded",
    }
    assert ("action", "log") in calls and ("action", "close") not in calls
    assert store.get_instance(iid)["status"] == "completed"


# --------------------------------------------------------------------------- #
# 3. AND 并行                                                                   #
# --------------------------------------------------------------------------- #


async def test_and_split_branches_run_concurrently() -> None:
    store = FakeStore()
    spec = _spec(
        [
            ScenarioStep(id="assess1", type="assess", rules_scope="gas_net"),
            ScenarioStep(id="br_a", action="ACT.A", requires=("assess1",)),
            ScenarioStep(id="br_b", action="ACT.B", requires=("assess1",)),
        ],
        [ScenarioGateway(id="gw1", type="and_split", branches=(("br_a",), ("br_b",)))],
    )
    iid = _seed_instance(store)

    a_started, b_started = asyncio.Event(), asyncio.Event()
    both_seen = asyncio.Event()

    async def run_action(action_id: str, step_id: str) -> dict[str, Any]:
        if step_id == "br_a":
            a_started.set()
            await asyncio.wait_for(b_started.wait(), timeout=5)
            both_seen.set()
            return {"status": "executed"}
        b_started.set()
        await asyncio.wait_for(a_started.wait(), timeout=5)
        return {"status": "executed"}

    async def run_assess(rules_scope: str | None) -> dict[str, Any]:
        return {"conclusions": [], "unruly": []}

    runner = ScenarioRunner(
        spec=spec, store=store, instance_id=iid,
        run_action=run_action, run_assess=run_assess, action_specs={},
    )
    await runner.run()

    assert both_seen.is_set()  # 真并发:两步同时在飞(gather 而非串行)
    runs = {r["step_id"]: r["status"] for r in store.list_step_runs(iid)}
    assert runs == {"assess1": "succeeded", "br_a": "succeeded", "br_b": "succeeded"}
    assert store.get_instance(iid)["status"] == "completed"


# --------------------------------------------------------------------------- #
# 4. requires 门槛 + 失败传播(无补偿 → failed)                                 #
# --------------------------------------------------------------------------- #


async def test_step_failure_stops_mainline_and_fails_instance() -> None:
    store = FakeStore()
    spec = _spec(
        [
            ScenarioStep(id="act_a", action="ACT.A"),
            ScenarioStep(id="act_b", action="ACT.B", requires=("act_a",)),
        ]
    )
    iid = _seed_instance(store)
    runner = _make_runner(
        store, spec, iid,
        action_impl={"act_a": RuntimeError("storage down")},
    )
    await runner.run()

    inst = store.get_instance(iid)
    assert inst is not None and inst["status"] == "failed"
    assert "act_a" in (inst["error"] or "")
    runs = {r["step_id"]: r["status"] for r in store.list_step_runs(iid)}
    assert runs == {"act_a": "failed"}  # act_b 从未启动


# --------------------------------------------------------------------------- #
# 5. 死锁守卫                                                                   #
# --------------------------------------------------------------------------- #


async def test_deadlock_guard_fails_instance() -> None:
    store = FakeStore()
    # 循环 requires:schema 只查引用存在,环由 runner 死锁守卫兜底
    spec = _spec(
        [
            ScenarioStep(id="act_a", action="ACT.A", requires=("act_b",)),
            ScenarioStep(id="act_b", action="ACT.B", requires=("act_a",)),
        ]
    )
    iid = _seed_instance(store)
    runner = _make_runner(store, spec, iid)
    await runner.run()

    inst = store.get_instance(iid)
    assert inst is not None and inst["status"] == "failed"
    assert "unsatisfiable" in (inst["error"] or "")


# --------------------------------------------------------------------------- #
# 6. 超时升级                                                                   #
# --------------------------------------------------------------------------- #


async def test_timeout_marks_step_runs_escalation_and_instance() -> None:
    store = FakeStore()
    spec = _spec(
        [
            ScenarioStep(id="act_slow", action="ACT.SLOW"),
            ScenarioStep(id="escalate", action="ACT.ESC"),
        ],
        timeout="PT30M",
        on_timeout="escalate",
    )
    # deadline 已过(instantiate 时刻即超时)
    iid = _seed_instance(store, deadline_at="2020-01-01T00:00:00Z")
    calls: list[tuple[str, str]] = []
    runner = _make_runner(store, spec, iid, calls=calls)
    await runner.run()

    inst = store.get_instance(iid)
    assert inst is not None and inst["status"] == "timeout"
    # 超时步标 timeout;on_timeout 升级步被执行
    runs = {r["step_id"]: r["status"] for r in store.list_step_runs(iid)}
    assert runs == {"act_slow": "timeout", "escalate": "succeeded"}
    assert calls == [("action", "escalate")]


async def test_timeout_without_escalation_step() -> None:
    store = FakeStore()
    spec = _spec([ScenarioStep(id="act_slow", action="ACT.SLOW")], timeout="PT1M")
    iid = _seed_instance(store, deadline_at="2020-01-01T00:00:00Z")
    runner = _make_runner(store, spec, iid)
    await runner.run()

    assert store.get_instance(iid)["status"] == "timeout"


# --------------------------------------------------------------------------- #
# 7. 补偿标记(声明式+人工)                                                      #
# --------------------------------------------------------------------------- #


async def test_failed_action_with_compensation_marks_pending() -> None:
    store = FakeStore()
    spec = _spec(
        [
            ScenarioStep(id="act_pub", action="ACT.PUB"),
            ScenarioStep(id="act_close", action="ACT.CLOSE", requires=("act_pub",)),
        ]
    )
    iid = _seed_instance(store)
    runner = _make_runner(
        store, spec, iid,
        action_impl={"act_pub": RuntimeError("notify burst failed")},
        action_specs={
            "ACT.PUB": _action_spec("ACT.PUB", compensation="ACT.UNPUB"),
            "ACT.CLOSE": _action_spec("ACT.CLOSE"),
        },
    )
    await runner.run()

    inst = store.get_instance(iid)
    assert inst is not None and inst["status"] == "compensated"
    assert json.loads(inst["pending_compensation_json"]) == ["ACT.UNPUB"]
    # 步行 output 记 pending_compensation(人工执行清单的落点)
    pub_run = next(r for r in store.list_step_runs(iid) if r["step_id"] == "act_pub")
    assert pub_run["status"] == "failed"
    assert json.loads(pub_run["output_json"])["pending_compensation"] == ["ACT.UNPUB"]


async def test_dead_letter_with_compensation_also_marks_pending() -> None:
    """dead_letter(效果归属不确定)与 failed 同触发补偿待办。"""
    store = FakeStore()
    spec = _spec([ScenarioStep(id="act_pub", action="ACT.PUB")])
    iid = _seed_instance(store)
    runner = _make_runner(
        store, spec, iid,
        action_impl={"act_pub": {"status": "dead_letter", "error": "storage io"}},
        action_specs={"ACT.PUB": _action_spec("ACT.PUB", compensation="ACT.UNPUB")},
    )
    await runner.run()

    inst = store.get_instance(iid)
    assert inst is not None and inst["status"] == "compensated"
    assert json.loads(inst["pending_compensation_json"]) == ["ACT.UNPUB"]


# --------------------------------------------------------------------------- #
# 8. 断点续跑(幂等重放)                                                        #
# --------------------------------------------------------------------------- #


async def test_resume_replays_via_already_in_effect() -> None:
    store = FakeStore()
    spec = _spec(
        [
            ScenarioStep(id="assess1", type="assess", rules_scope="gas_net"),
            ScenarioStep(id="act_a", action="ACT.A", requires=("assess1",)),
            ScenarioStep(id="act_b", action="ACT.B", requires=("act_a",)),
        ]
    )
    iid = _seed_instance(store)
    calls: list[tuple[str, str]] = []

    # 首跑:act_b 失败 → 实例 failed
    runner1 = _make_runner(
        store, spec, iid, calls=calls,
        action_impl={"act_b": RuntimeError("transient")},
    )
    await runner1.run()
    assert store.get_instance(iid)["status"] == "failed"

    # resume 前置:实例行回 running(router resume 端点职责)
    store.update_instance(iid, status="running", error=None)

    # 续跑:act_b 修好;act_a 已 succeeded 不重跑(step_runs 是进度 SoT——
    # 盲重放无 idempotency_key 的 action 会双写)
    runner2 = _make_runner(
        store, spec, iid, calls=calls,
        action_impl={"act_b": {"status": "executed"}},
    )
    await runner2.run(resume=True)

    inst = store.get_instance(iid)
    assert inst is not None and inst["status"] == "completed"
    assert calls.count(("action", "act_a")) == 1
    assert calls.count(("action", "act_b")) == 2  # 失败重试
    # assess 纯求值重跑
    assert calls.count(("assess", "gas_net")) == 2


async def test_resume_crashed_running_step_replays_via_idempotency() -> None:
    """崩溃窗口:效果已落但步行卡 running(worker 在 effect 与落行间死)
    → resume 重放经幂等 already_in_effect 视作 succeeded(设计 §1.2)。"""
    store = FakeStore()
    spec = _spec(
        [
            ScenarioStep(id="act_a", action="ACT.A"),
            ScenarioStep(id="act_b", action="ACT.B", requires=("act_a",)),
        ]
    )
    iid = _seed_instance(store)
    calls: list[tuple[str, str]] = []

    # 首跑:act_a 执行成功,但模拟崩溃——步行被留在 running、实例 failed
    async def crash_after_a(action_id: str, step_id: str) -> dict[str, Any]:
        if calls is not None:
            calls.append(("action", step_id))
        return {"status": "executed"}

    runner1 = ScenarioRunner(
        spec=spec, store=store, instance_id=iid,
        run_action=crash_after_a, run_assess=_noop_assess, action_specs={},
    )
    await runner1.run()
    # 模拟:act_b 从未跑;act_a 行为 succeeded(首跑正常完成)
    # 再人为把 act_a 行拨回 running = 崩溃窗口形态
    store.finish_step(iid, "act_a", "action", "running")
    store.update_instance(iid, status="failed", error="orphaned runner")
    store.update_instance(iid, status="running", error=None)

    async def replay_a(action_id: str, step_id: str) -> dict[str, Any]:
        if calls is not None:
            calls.append(("action", step_id))
        if step_id == "act_a":
            return {"status": "already_in_effect"}  # 幂等重放
        return {"status": "executed"}

    runner2 = ScenarioRunner(
        spec=spec, store=store, instance_id=iid,
        run_action=replay_a, run_assess=_noop_assess, action_specs={},
    )
    await runner2.run(resume=True)

    inst = store.get_instance(iid)
    assert inst is not None and inst["status"] == "completed"
    runs = {r["step_id"]: r["status"] for r in store.list_step_runs(iid)}
    assert runs == {"act_a": "succeeded", "act_b": "succeeded"}


# --------------------------------------------------------------------------- #
# 9. terminate 外部终止                                                         #
# --------------------------------------------------------------------------- #


async def test_terminate_stops_runner() -> None:
    store = FakeStore()
    spec = _spec(
        [
            ScenarioStep(id="act_a", action="ACT.A"),
            ScenarioStep(id="act_b", action="ACT.B", requires=("act_a",)),
        ]
    )
    iid = _seed_instance(store)
    calls: list[tuple[str, str]] = []

    async def run_action(action_id: str, step_id: str) -> dict[str, Any]:
        if calls is not None:
            calls.append(("action", step_id))
        if step_id == "act_a":
            # act_a 完成的同时外部 terminate(router terminate 端点职责)
            store.update_instance(iid, status="terminated", finished=True)
        return {"status": "executed"}

    async def run_assess(rules_scope: str | None) -> dict[str, Any]:
        return {"conclusions": [], "unruly": []}

    runner = ScenarioRunner(
        spec=spec, store=store, instance_id=iid,
        run_action=run_action, run_assess=run_assess, action_specs={},
    )
    await runner.run()

    inst = store.get_instance(iid)
    assert inst is not None and inst["status"] == "terminated"
    assert calls == [("action", "act_a")]  # act_b 未再执行
    runs = {r["step_id"]: r["status"] for r in store.list_step_runs(iid)}
    assert "act_b" not in runs


# --------------------------------------------------------------------------- #
# 10. manual_intervention / dead_letter 步 → 实例 failed                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("step_status", ["manual_intervention", "dead_letter"])
async def test_manual_statuses_fail_instance(step_status: str) -> None:
    store = FakeStore()
    spec = _spec([ScenarioStep(id="act_a", action="ACT.A")])
    iid = _seed_instance(store)
    runner = _make_runner(store, spec, iid, action_impl={"act_a": {"status": step_status}})
    await runner.run()

    inst = store.get_instance(iid)
    assert inst is not None and inst["status"] == "failed"
    assert "act_a" in (inst["error"] or "")
    runs = {r["step_id"]: r["status"] for r in store.list_step_runs(iid)}
    assert runs == {"act_a": step_status}
