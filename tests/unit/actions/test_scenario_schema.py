"""W1.3 (v1.11.2 MS3 F3.5) — Scenario 场景模型 + 保存期引用校验。

规范:ms3-modeling-language-design.md §3。Scenario = 规范+审计词表,
非执行引擎(S3);保存期校验纪律:① steps 引用的 action 必须在行动目录
(对 catalog stub 集合校验);② requires/gateways 引用的 step id 必须存在;
③ xor 强制 else(替代路径对等表达);④ entries/when 谓词可解析;
⑤ timeout 合法 ISO-8601 duration(手写正则,零依赖)。

注:规范示例的 and_split 分支引用 notify_ops 但未声明该 step(示例笔误),
本 fixture 补齐该 step——保存期纪律要求分支引用必须存在。
"""

from __future__ import annotations

import re

import pytest
from arrow_lake.actions.schema import (
    ScenarioSpec,
    ScenarioValidationError,
    validate_scenario,
)
from pydantic import ValidationError

_KNOWN_ACTIONS = {
    "GAS.ALERT.PUBLISH",
    "GAS.ALERT.ESCALATE",
    "GAS.ALERT.NOTIFY",
    "GAS.ALERT.WITHDRAW",
}


def _spec() -> dict:
    """设计文档 §3 的规范示例(GAS.LEAK.RESPONSE,补齐 notify_ops)。"""
    return {
        "scenario_id": "GAS.LEAK.RESPONSE",
        "title": "燃气泄漏告警响应",
        "process": "告警研判与预警发布",
        "entries": ["target.lifecycle_state == '待研判'"],
        "steps": [
            {"id": "assess", "type": "assess", "rules_scope": "gas_network"},
            {"id": "publish", "action": "GAS.ALERT.PUBLISH", "requires": ["assess"]},
            {
                "id": "escalate_manual",
                "action": "GAS.ALERT.ESCALATE",
                "path": "substitute",
                "requires": ["assess"],
            },
            {"id": "notify_ops", "action": "GAS.ALERT.NOTIFY", "requires": ["publish"]},
        ],
        "gateways": [
            {
                "id": "confidence_gate",
                "type": "xor",
                "when": "assess.confidence >= 0.8 && assess.matched_rules >= 1",
                "then": ["publish"],
                "else": ["escalate_manual"],
            },
            {
                "id": "parallel_notify",
                "type": "and_split",
                "branches": [["publish"], ["notify_ops"]],
            },
        ],
        "timeout": "PT30M",
        "on_timeout": "escalate_manual",
    }


def _validated(mutate=None) -> ScenarioSpec:
    data = _spec()
    if mutate:
        mutate(data)
    return ScenarioSpec.model_validate(data)


class TestCanonicalExample:
    def test_design_example_valid(self) -> None:
        spec = _validated()
        validate_scenario(spec, _KNOWN_ACTIONS)
        assert spec.scenario_id == "GAS.LEAK.RESPONSE"
        assert spec.steps[0].type == "assess"


class TestModelShape:
    def test_step_must_be_assess_or_action(self) -> None:
        with pytest.raises(ValidationError):
            ScenarioSpec.model_validate({**_spec(), "steps": [{"id": "orphan", "requires": []}]})

    def test_step_cannot_be_both(self) -> None:
        with pytest.raises(ValidationError):
            ScenarioSpec.model_validate(
                {**_spec(), "steps": [{"id": "x", "type": "assess", "action": "GAS.ALERT.PUBLISH"}]}
            )

    def test_duplicate_step_ids_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _validated(lambda d: d["steps"].append({"id": "publish", "action": "GAS.ALERT.NOTIFY"}))

    def test_xor_requires_else(self) -> None:
        def drop_else(d: dict) -> None:
            d["gateways"] = [{"id": "g", "type": "xor", "when": "true", "then": ["publish"]}]

        with pytest.raises(ValidationError):
            _validated(drop_else)

    def test_unparseable_when_rejected(self) -> None:
        def bad_when(d: dict) -> None:
            d["gateways"] = [
                {
                    "id": "g",
                    "type": "xor",
                    "when": "nope ==",
                    "then": ["publish"],
                    "else": ["notify_ops"],
                }
            ]

        with pytest.raises(ValidationError):
            _validated(bad_when)

    def test_unparseable_entries_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _validated(lambda d: d.update(entries=["bad =="]))

    def test_timeout_iso8601_duration(self) -> None:
        for ok in ("PT30M", "P1DT12H", "PT90S"):
            _validated(lambda d, t=ok: d.update(timeout=t))
        for bad in ("30min", "PT", "T5M", "P"):
            with pytest.raises(ValidationError):
                _validated(lambda d, t=bad: d.update(timeout=t))


class TestCrossReferences:
    def test_dangling_action_reference(self) -> None:
        spec = _validated(lambda d: d["steps"].append({"id": "extra", "action": "NOT.IN.CATALOG"}))
        with pytest.raises(ScenarioValidationError, match=re.escape("NOT.IN.CATALOG")):
            validate_scenario(spec, _KNOWN_ACTIONS)

    def test_requires_unknown_step(self) -> None:
        def mutate(d: dict) -> None:
            d["steps"][1]["requires"] = ["no_such_step"]

        with pytest.raises(ScenarioValidationError, match="no_such_step"):
            validate_scenario(_validated(mutate), _KNOWN_ACTIONS)

    def test_step_cannot_require_itself(self) -> None:
        def mutate(d: dict) -> None:
            d["steps"][1]["requires"] = ["publish"]

        with pytest.raises(ScenarioValidationError):
            validate_scenario(_validated(mutate), _KNOWN_ACTIONS)

    def test_gateway_then_unknown_step(self) -> None:
        def mutate(d: dict) -> None:
            d["gateways"][0]["then"] = ["nope"]

        with pytest.raises(ScenarioValidationError, match="confidence_gate"):
            validate_scenario(_validated(mutate), _KNOWN_ACTIONS)

    def test_and_split_branch_unknown_step(self) -> None:
        def mutate(d: dict) -> None:
            d["gateways"][1]["branches"] = [["publish"], ["ghost"]]

        with pytest.raises(ScenarioValidationError, match="parallel_notify"):
            validate_scenario(_validated(mutate), _KNOWN_ACTIONS)

    def test_on_timeout_unknown_step(self) -> None:
        with pytest.raises(ScenarioValidationError, match="on_timeout"):
            validate_scenario(_validated(lambda d: d.update(on_timeout="nope")), _KNOWN_ACTIONS)

    def test_all_issues_collected(self) -> None:
        # 多处违规一次性收齐(console 回显友好),不只报第一条
        def mutate(d: dict) -> None:
            d["steps"][1]["requires"] = ["ghost1"]
            d["steps"].append({"id": "extra", "action": "NOT.IN.CATALOG"})
            d["on_timeout"] = "ghost2"

        spec = _validated(mutate)
        with pytest.raises(ScenarioValidationError) as exc_info:
            validate_scenario(spec, _KNOWN_ACTIONS)
        joined = "\n".join(exc_info.value.issues)
        assert "ghost1" in joined
        assert "NOT.IN.CATALOG" in joined
        assert "ghost2" in joined
