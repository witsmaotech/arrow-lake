"""模板插值求值器(v1.11.2 MS3 W1.4,建模语言 §4.3)。

``{{ path }}`` 与 ``{{ now() }}`` **仅此两种形态**(变量引用/当前时间戳),
封闭实现,不开放表达式。供 Action schema 保存期校验(to_state/幂等键/
fields/payload)与 W4 中间件执行期渲染共用。

事件 payload 项 = bare path 或纯单占位模板,渲染返回**原值**(list/dict
原样,非字符串化)——post_event 携带结构化数据;字符串场景(fields 值、
幂等键、to_state)用 :func:`render_template`,缺失 path 渲染为可配缺省
(默认空串)。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from arrow_lake.actions.predicates import is_valid_path, resolve_path

__all__ = [
    "TemplateError",
    "render_payload_item",
    "render_template",
    "validate_payload_item",
    "validate_template",
]


class TemplateError(ValueError):
    """模板形态越权(非 {{ path }} / {{ now() }})或括号不配平。"""


_PLACEHOLDER_RE = re.compile(r"\{\{\s*(.*?)\s*\}\}")
_PURE_PLACEHOLDER_RE = re.compile(r"^\{\{\s*(.*?)\s*\}\}$")
_NOW_CALL_RE = re.compile(r"^now\(\)$")


def _default_now() -> str:
    return datetime.now(UTC).isoformat()


def _check_placeholder(inner: str) -> None:
    if _NOW_CALL_RE.match(inner):
        return
    if inner == "now":
        # 裸 "now" 必是漏写括号的 {{ now() }}——封闭两形态,直接拒
        raise TemplateError("{{ now }} is reserved — did you mean {{ now() }}?")
    if is_valid_path(inner):
        return
    raise TemplateError(
        f"invalid placeholder {{{{ {inner} }}}}: only {{{{ path }}}} / {{{{ now() }}}} are allowed"
    )


def validate_template(template: str) -> None:
    """保存期校验:每个占位符必须是 {{ path }} 或 {{ now() }}。"""
    if template.count("{{") != template.count("}}"):
        raise TemplateError(f"unbalanced {{{{ }}}} in template: {template!r}")
    for m in _PLACEHOLDER_RE.finditer(template):
        _check_placeholder(m.group(1))


def render_template(
    template: str,
    context: Mapping[str, Any],
    *,
    now: Callable[[], str] | None = None,
    missing: str = "",
) -> str:
    """渲染字符串模板(fields 值/幂等键/to_state 形态)。

    缺失 path → ``missing``(默认空串;幂等键空值由中间件把关)。
    ``now`` 可注入(测试确定性时钟)。
    """
    validate_template(template)
    clock = now or _default_now

    def _sub(m: re.Match[str]) -> str:
        inner = m.group(1)
        if _NOW_CALL_RE.match(inner):
            return clock()
        value = resolve_path(context, tuple(inner.split(".")))
        return missing if value is None else str(value)

    return _PLACEHOLDER_RE.sub(_sub, template)


def validate_payload_item(item: str) -> None:
    """payload 项形态:bare path,或纯单占位模板(整体恰为一个占位符)。"""
    m = _PURE_PLACEHOLDER_RE.match(item)
    if m is not None:
        _check_placeholder(m.group(1))
        return
    if is_valid_path(item):
        return
    raise TemplateError(
        f"invalid payload item {item!r}: bare path or {{{{ path }}}} / {{{{ now() }}}} only"
    )


def render_payload_item(
    item: str,
    context: Mapping[str, Any],
    *,
    now: Callable[[], str] | None = None,
) -> Any:
    """渲染 payload 项 → **原值**(非字符串化);缺失 path → None。"""
    validate_payload_item(item)
    clock = now or _default_now
    inner: str
    m = _PURE_PLACEHOLDER_RE.match(item)
    if m is not None:
        inner = m.group(1)
        if _NOW_CALL_RE.match(inner):
            return clock()
    else:
        inner = item
    return resolve_path(context, tuple(inner.split(".")))
