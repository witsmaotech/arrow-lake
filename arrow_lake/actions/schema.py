"""Action/Scenario 模型(v1.11.2 MS3 W1.2/W1.3,建模语言 §2/§3 代码化)。

保存期校验纪律:谓词可解析、模板形态封闭({{path}}/{{now()}})、effect
封闭集(S1)、M6 枚举(补偿仅 manual/异常四分类/fallback 三枚举)、
permission=scope 形态、ISO-8601 duration。**跨引用校验**(scenario 引
action 须在目录、step/gateway 引用存在)在 :func:`validate_scenario`
——需要 catalog 集合,模型自身只做局部形状。

pydantic ValidationError → 路由层 422(W2.3 接线)。
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from arrow_lake.actions.predicates import (
    ParsedPredicateError,
    compile_predicate,
    is_valid_path,
)
from arrow_lake.actions.templates import validate_payload_item, validate_template

__all__ = [
    "EFFECT_TYPES",
    "EXCEPTION_CLASSES",
    "FALLBACKS",
    "IDENTITY_MODES",
    "ActionEffect",
    "ActionSpec",
    "ActionTarget",
    "AuditSpec",
    "Compensation",
    "OnFailure",
    "PostEvent",
    "ScenarioGateway",
    "ScenarioSpec",
    "ScenarioStep",
    "ScenarioValidationError",
    "validate_scenario",
]

EFFECT_TYPES = ("update_lifecycle", "notify", "none")  # S1 封闭集
IDENTITY_MODES = ("contract_identifier", "entity_map")  # W2 双路径
COMPENSATION_POLICIES = ("manual",)  # S4 首版仅 manual
FALLBACKS = ("MANUAL", "DEAD_LETTER", "REJECT")
EXCEPTION_CLASSES = (
    "business",
    "technical",
    "conflict",
    "compensation_failed",  # M6 四分类
)

# 域.对象类.行为(GAS.ALERT.PUBLISH)/事件名(alert.published)同形态:
# unicode 词字符 + 点分段,无空格/符号
_DOTTED_ID_RE = re.compile(r"^\w+(\.\w+)*$")
# require_permission 的 scope 形态(dataset:read / alerts:publish)小写
_PERMISSION_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_-]*:[a-z][a-z0-9_-]*$")
_STEP_ID_RE = re.compile(r"^\w+$")
# 数据集/对象类名:非空,拒引号/分号/控制符/空白(沿契约列名黑名单风格)
_UNSAFE_NAME_RE = re.compile(r'["\';\x00-\x1f\s]')
# ISO-8601 duration(零依赖手写;P 与 T 后均须至少一位数,防 "PT"/"P")
_ISO8601_DURATION_RE = re.compile(
    r"^P(?!$)(?:\d+Y)?(?:\d+M)?(?:\d+W)?(?:\d+D)?"
    r"(?:T(?=.)(?:\d+H)?(?:\d+M)?(?:\d+(?:\.\d+)?S)?)?$"
)


def _parse_predicate_or_raise(expr: str, field_label: str) -> None:
    try:
        compile_predicate(expr)
    except ParsedPredicateError as exc:
        raise ValueError(f"{field_label} not parseable ({expr!r}): {exc}") from exc


# --------------------------------------------------------------------------
# Action(§2 原子行为)
# --------------------------------------------------------------------------


class ActionTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset: str
    object_class: str
    identity: Literal["contract_identifier", "entity_map"] = "contract_identifier"

    @field_validator("dataset", "object_class")
    @classmethod
    def _safe_name(cls, v: str, info) -> str:
        if not v.strip() or _UNSAFE_NAME_RE.search(v):
            raise ValueError(f"unsafe {info.field_name}: {v!r}")
        return v


class ActionEffect(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["update_lifecycle", "notify", "none"]
    to_state: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)

    @field_validator("to_state")
    @classmethod
    def _to_state_template(cls, v: str | None) -> str | None:
        if v is not None:
            validate_template(v)
        return v

    @field_validator("fields")
    @classmethod
    def _fields_templates(cls, v: dict[str, str]) -> dict[str, str]:
        for template in v.values():
            validate_template(template)
        return v

    @model_validator(mode="after")
    def _to_state_presence(self) -> ActionEffect:
        if self.type == "update_lifecycle" and self.to_state is None:
            raise ValueError("update_lifecycle effect requires to_state")
        if self.type != "update_lifecycle" and self.to_state is not None:
            raise ValueError(f"to_state only applies to update_lifecycle (effect is {self.type!r})")
        return self


class Compensation(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: str
    policy: Literal["manual"] = "manual"

    @field_validator("action")
    @classmethod
    def _action_shape(cls, v: str) -> str:
        if not _DOTTED_ID_RE.match(v):
            raise ValueError(f"compensation action id malformed: {v!r}")
        return v


class OnFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    fallback: Literal["MANUAL", "DEAD_LETTER", "REJECT"] = "REJECT"
    exception_class: Literal["business", "technical", "conflict", "compensation_failed"] = (
        "technical"
    )


class AuditSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason_required: bool = True
    include: tuple[str, ...] = ()  # 审计携带研判依据 → 依据可溯率(F3.6)

    @field_validator("include")
    @classmethod
    def _include_paths(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for item in v:
            if not is_valid_path(item):
                raise ValueError(f"audit.include items must be paths, got {item!r}")
        return v


class PostEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    payload: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _name_shape(cls, v: str) -> str:
        if not _DOTTED_ID_RE.match(v):
            raise ValueError(f"post_event name malformed: {v!r}")
        return v

    @field_validator("payload")
    @classmethod
    def _payload_items(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for item in v:
            validate_payload_item(item)
        return v


class ActionSpec(BaseModel):
    """行动目录条目(§2;一事一对象,锚定一个目标对象类)。"""

    model_config = ConfigDict(frozen=True)

    action_id: str
    title: str
    target: ActionTarget
    permission: str | None = None  # 缺省=仅认证(require_permission scope)
    preconditions: tuple[str, ...] = ()
    effect: ActionEffect
    idempotency_key: str | None = None
    compensation: Compensation | None = None
    on_failure: OnFailure = Field(default_factory=OnFailure)
    audit: AuditSpec = Field(default_factory=AuditSpec)
    post_event: PostEvent | None = None

    @field_validator("action_id")
    @classmethod
    def _action_id_shape(cls, v: str) -> str:
        if not _DOTTED_ID_RE.match(v):
            raise ValueError(f"action_id malformed (域.对象类.行为, word chars + dots): {v!r}")
        return v

    @field_validator("title")
    @classmethod
    def _title_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must be non-empty")
        return v

    @field_validator("permission")
    @classmethod
    def _permission_scope(cls, v: str | None) -> str | None:
        if v is not None and not _PERMISSION_SCOPE_RE.match(v):
            raise ValueError(f"permission must be a lowercase scope (ns:action), got {v!r}")
        return v

    @field_validator("preconditions")
    @classmethod
    def _preconditions_parseable(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for expr in v:
            _parse_predicate_or_raise(expr, "precondition")
        return v

    @field_validator("idempotency_key")
    @classmethod
    def _idempotency_template(cls, v: str | None) -> str | None:
        if v is not None:
            validate_template(v)
        return v


# --------------------------------------------------------------------------
# Scenario(§3 场景编排——规范+审计词表,非执行引擎,S3)
# --------------------------------------------------------------------------


class ScenarioStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    type: Literal["assess"] | None = None  # 研判步(F3.1),非 action
    action: str | None = None
    rules_scope: str | None = None  # 仅 assess 步
    requires: tuple[str, ...] = ()
    path: Literal["main", "substitute"] | None = None

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not _STEP_ID_RE.match(v):
            raise ValueError(f"step id malformed: {v!r}")
        return v

    @field_validator("action")
    @classmethod
    def _action_shape(cls, v: str | None) -> str | None:
        if v is not None and not _DOTTED_ID_RE.match(v):
            raise ValueError(f"step action reference malformed: {v!r}")
        return v

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> ScenarioStep:
        is_assess = self.type == "assess"
        if is_assess == (self.action is not None):
            raise ValueError("step must be either type=assess or action=… (not both, not neither)")
        if not is_assess and self.rules_scope is not None:
            raise ValueError("rules_scope only applies to assess steps")
        return self


class ScenarioGateway(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: str
    type: Literal["xor", "and_split"]
    when: str | None = None
    then: tuple[str, ...] = ()
    else_: tuple[str, ...] = Field(default=(), alias="else")
    branches: tuple[tuple[str, ...], ...] = ()

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not _STEP_ID_RE.match(v):
            raise ValueError(f"gateway id malformed: {v!r}")
        return v

    @model_validator(mode="after")
    def _xor_shape(self) -> ScenarioGateway:
        if self.type == "xor":
            # M4「异常路径对等表达」:xor 强制 else 替代路径
            if not self.else_:
                raise ValueError("xor gateway requires else (替代路径)")
            if self.when is None:
                raise ValueError("xor gateway requires when")
        if self.when is not None:
            _parse_predicate_or_raise(self.when, "gateway when")
        return self


class ScenarioSpec(BaseModel):
    """场景条目(§3;entries/when 谓词 DSL,timeout 声明性)。"""

    model_config = ConfigDict(frozen=True)

    scenario_id: str
    title: str
    process: str | None = None
    entries: tuple[str, ...] = ()
    steps: tuple[ScenarioStep, ...] = Field(min_length=1)
    gateways: tuple[ScenarioGateway, ...] = ()
    timeout: str | None = None
    on_timeout: str | None = None

    @field_validator("scenario_id")
    @classmethod
    def _scenario_id_shape(cls, v: str) -> str:
        if not _DOTTED_ID_RE.match(v):
            raise ValueError(f"scenario_id malformed: {v!r}")
        return v

    @field_validator("title")
    @classmethod
    def _title_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must be non-empty")
        return v

    @field_validator("entries")
    @classmethod
    def _entries_parseable(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for expr in v:
            _parse_predicate_or_raise(expr, "entry")
        return v

    @field_validator("timeout")
    @classmethod
    def _timeout_duration(cls, v: str | None) -> str | None:
        if v is not None and not _ISO8601_DURATION_RE.match(v):
            raise ValueError(f"timeout must be ISO-8601 duration (PT30M), got {v!r}")
        return v

    @model_validator(mode="after")
    def _unique_step_ids(self) -> ScenarioSpec:
        ids = [s.id for s in self.steps]
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate step ids: {dupes}")
        return self


class ScenarioValidationError(ValueError):
    """跨引用校验失败;issues 收齐全部问题(console 回显友好)。"""

    def __init__(self, issues: list[str]) -> None:
        self.issues = list(issues)
        super().__init__("; ".join(issues))


def validate_scenario(spec: ScenarioSpec, known_action_ids: Collection[str]) -> None:
    """保存期跨引用校验(纪律 ①②):action 在目录、step/gateway 引用存在。

    全部问题一次性收齐再抛;catalog 引用悬空 → ScenarioValidationError
    (路由层 422,W2.3 接线)。
    """
    issues: list[str] = []
    step_ids = {s.id for s in spec.steps}
    known = set(known_action_ids)

    for step in spec.steps:
        if step.action is not None and step.action not in known:
            issues.append(f"step '{step.id}' references action '{step.action}' not in catalog")
        for req in step.requires:
            if req == step.id:
                issues.append(f"step '{step.id}' cannot require itself")
            elif req not in step_ids:
                issues.append(f"step '{step.id}' requires unknown step '{req}'")

    for gw in spec.gateways:
        for label, refs in (("then", gw.then), ("else", gw.else_)):
            for ref in refs:
                if ref not in step_ids:
                    issues.append(f"gateway '{gw.id}' {label} references unknown step '{ref}'")
        for bi, branch in enumerate(gw.branches):
            if not branch:
                issues.append(f"gateway '{gw.id}' branch[{bi}] is empty")
            for ref in branch:
                if ref not in step_ids:
                    issues.append(f"gateway '{gw.id}' branch[{bi}] references unknown step '{ref}'")

    if spec.on_timeout is not None and spec.on_timeout not in step_ids:
        issues.append(f"on_timeout '{spec.on_timeout}' references unknown step")

    if issues:
        raise ScenarioValidationError(issues)
