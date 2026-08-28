"""W1.4 (v1.11.2 MS3) — 模板插值求值器({{ path }} / {{ now() }} 仅此两形态)。

规范:ms3-modeling-language-design.md §4.3。封闭实现,不开放表达式;
供 Action schema 保存期校验(to_state/幂等键/fields/payload)与 W4
中间件执行期渲染共用。事件 payload 项=bare path 或纯单占位模板,渲染
返回**原值**(非字符串化),供 post_event 携带结构化数据。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from arrow_lake.actions.templates import (
    TemplateError,
    render_payload_item,
    render_template,
    validate_payload_item,
    validate_template,
)

_CTX = {
    "target": {"object_id": "AL-001", "风险等级": "高"},
    "assess": {"level": "橙色", "rule_ids": ["R1", "R2"]},
    "actor": {"sub": "u1", "role": "sysop"},
}


class TestRenderTemplate:
    def test_literal_passthrough(self) -> None:
        assert render_template("已发布", _CTX) == "已发布"

    def test_path_substitution(self) -> None:
        assert render_template("{{ target.object_id }}", _CTX) == "AL-001"
        assert render_template("{{target.风险等级}}", _CTX) == "高"

    def test_mixed_literal_and_placeholders(self) -> None:
        out = render_template("level={{ assess.level }} id={{ target.object_id }}", _CTX)
        assert out == "level=橙色 id=AL-001"

    def test_now_renders_iso_timestamp(self) -> None:
        fixed = datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC)
        out = render_template("{{ now() }}", _CTX, now=lambda: fixed.isoformat())
        assert out == "2026-08-28T09:00:00+00:00"

    def test_missing_path_renders_default(self) -> None:
        assert render_template("{{ target.nope }}", _CTX) == ""
        assert render_template("{{ target.nope }}", _CTX, missing="∅") == "∅"


class TestValidateTemplate:
    def test_valid_forms_accepted(self) -> None:
        validate_template("已发布")  # 纯字面量(无占位符)= 合法模板
        validate_template("{{ target.object_id }}")
        validate_template("x {{ now() }} y {{ assess.level }}")

    def test_invalid_placeholder_forms_rejected(self) -> None:
        for bad in ["{{ 1+1 }}", "{{ foo() }}", "{{ now }}", "{{ a b }}", "{{ }}", "{{ a.b. }}"]:
            with pytest.raises(TemplateError):
                validate_template(bad)

    def test_unclosed_placeholder_rejected(self) -> None:
        with pytest.raises(TemplateError):
            validate_template("x {{ target.object_id")

    def test_render_rejects_like_validate(self) -> None:
        with pytest.raises(TemplateError):
            render_template("{{ 1+1 }}", _CTX)


class TestPayloadItems:
    def test_valid_payload_item_forms(self) -> None:
        validate_payload_item("target.object_id")
        validate_payload_item("{{ target.object_id }}")
        validate_payload_item("{{ now() }}")

    def test_invalid_payload_item_rejected(self) -> None:
        for bad in ["two words", "{{ a }} {{ b }}", "x{{ a }}", "", "a.b."]:
            with pytest.raises(TemplateError):
                validate_payload_item(bad)

    def test_render_payload_item_returns_native_value(self) -> None:
        # payload 项渲染返回原值(list/dict 原样),非字符串化
        assert render_payload_item("assess.rule_ids", _CTX) == ["R1", "R2"]
        assert render_payload_item("actor", _CTX) == {"sub": "u1", "role": "sysop"}

    def test_render_payload_item_missing_is_none(self) -> None:
        assert render_payload_item("target.nope.deep", _CTX) is None
