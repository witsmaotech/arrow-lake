"""W1.2 (v1.11.2 MS3 F3.3) — Action 行动目录条目模型(保存期校验)。

规范:ms3-modeling-language-design.md §2。effect 封闭集 S1
{update_lifecycle, notify, none};模板位(to_state/幂等键/fields/
payload)仅 {{path}}/{{now()}} 两形态;compensation 首版仅 manual
(S4);on_failure 四分类;permission = require_permission 的 scope
形态(小写 ns:action,如 alerts:publish;缺省=仅认证)。
"""

from __future__ import annotations

import pytest
from arrow_lake.actions.schema import ActionSpec
from pydantic import ValidationError


def _spec(**overrides) -> dict:
    """设计文档 §2 的规范示例(GAS.ALERT.PUBLISH)。"""
    base = {
        "action_id": "GAS.ALERT.PUBLISH",
        "title": "发布燃气泄漏预警",
        "target": {
            "dataset": "gas_network",
            "object_class": "告警事件",
            "identity": "contract_identifier",
        },
        "permission": "alerts:publish",
        "preconditions": [
            "target.lifecycle_state == '研判确认'",
            "assess.confidence >= 0.8",
        ],
        "effect": {
            "type": "update_lifecycle",
            "to_state": "已发布",
            "fields": {"level": "{{ assess.level }}", "published_at": "{{ now() }}"},
        },
        "idempotency_key": "{{ target.object_id }}",
        "compensation": {"action": "GAS.ALERT.WITHDRAW", "policy": "manual"},
        "on_failure": {"fallback": "DEAD_LETTER", "exception_class": "technical"},
        "audit": {"reason_required": True, "include": ["assess.rule_ids"]},
        "post_event": {
            "name": "alert.published",
            "payload": ["target.object_id", "assess.rule_ids", "actor"],
        },
    }
    base.update(overrides)
    return base


class TestCanonicalExample:
    def test_design_example_parses(self) -> None:
        action = ActionSpec.model_validate(_spec())
        assert action.action_id == "GAS.ALERT.PUBLISH"
        assert action.effect.type == "update_lifecycle"
        assert action.target.identity == "contract_identifier"

    def test_minimal_action_effect_none(self) -> None:
        action = ActionSpec.model_validate(
            {
                "action_id": "OPS.TICKET.LOG",
                "title": "登记",
                "target": {"dataset": "ops", "object_class": "工单"},
                "effect": {"type": "none"},
            }
        )
        assert action.permission is None
        assert action.idempotency_key is None
        assert action.on_failure.fallback == "REJECT"  # 保守默认


class TestEffectClosedSet:
    def test_unknown_effect_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(_spec(effect={"type": "delete_row"}))

    def test_update_lifecycle_requires_to_state(self) -> None:
        eff = {"type": "update_lifecycle", "fields": {}}
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(_spec(effect=eff))

    def test_to_state_rejected_for_notify(self) -> None:
        eff = {"type": "notify", "to_state": "已发布"}
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(_spec(effect=eff))

    def test_effect_field_template_invalid_rejected(self) -> None:
        eff = {"type": "update_lifecycle", "to_state": "已发布", "fields": {"x": "{{ 1+1 }}"}}
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(_spec(effect=eff))


class TestTemplatesAndPermission:
    def test_idempotency_key_template_invalid_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(_spec(idempotency_key="{{ foo() }}"))

    def test_permission_scope_form(self) -> None:
        assert ActionSpec.model_validate(_spec(permission="alerts:publish"))
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(_spec(permission="Alerts:publish"))
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(_spec(permission="publish"))


class TestPredicatesAndReferences:
    def test_unparseable_precondition_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(_spec(preconditions=["nope =="]))

    def test_action_id_pattern(self) -> None:
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(_spec(action_id="GAS ALERT PUBLISH"))
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(_spec(action_id=""))

    def test_identity_enum(self) -> None:
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(
                _spec(
                    target={
                        "dataset": "gas_network",
                        "object_class": "告警事件",
                        "identity": "whatever",
                    }
                )
            )


class TestM6Semantics:
    def test_compensation_policy_only_manual(self) -> None:
        comp = {"action": "GAS.ALERT.WITHDRAW", "policy": "auto"}
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(_spec(compensation=comp))

    def test_on_failure_enums(self) -> None:
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(
                _spec(on_failure={"fallback": "RETRY", "exception_class": "technical"})
            )
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(
                _spec(on_failure={"fallback": "REJECT", "exception_class": "fatal"})
            )

    def test_valid_exception_classes_accepted(self) -> None:
        for cls in ("business", "technical", "conflict", "compensation_failed"):
            ActionSpec.model_validate(
                _spec(on_failure={"fallback": "DEAD_LETTER", "exception_class": cls})
            )


class TestAuditAndEvent:
    def test_audit_include_must_be_paths(self) -> None:
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(
                _spec(audit={"reason_required": True, "include": ["not a path"]})
            )

    def test_post_event_payload_invalid_item_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(
                _spec(post_event={"name": "alert.published", "payload": ["two words"]})
            )

    def test_post_event_name_nonempty(self) -> None:
        with pytest.raises(ValidationError):
            ActionSpec.model_validate(_spec(post_event={"name": "", "payload": []}))
