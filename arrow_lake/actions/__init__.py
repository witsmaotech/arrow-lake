"""决策与行动层(v1.11.2 MS3)——语言地基(W1:谓词 DSL/模板/Action/Scenario)。

规范:docs_offline/ms3-modeling-language-design.md(v1.1,S1-S10 已批)。
旁路新模块,零热路径影响:不被 ``arrow_lake/__init__.py`` 引入,摄入/OLAP/
semantic 不感知。后续:W2 存储 V016/V017+管理 API、W3 研判引擎
(decisions/)、W4 中间件 middleware.py+事件 events.py。
"""

from arrow_lake.actions.predicates import (
    ParsedPredicate,
    ParsedPredicateError,
    compile_predicate,
    evaluate,
    evaluate_expr,
)
from arrow_lake.actions.schema import (
    EFFECT_TYPES,
    EXCEPTION_CLASSES,
    FALLBACKS,
    IDENTITY_MODES,
    ActionEffect,
    ActionSpec,
    ActionTarget,
    AuditSpec,
    Compensation,
    OnFailure,
    PostEvent,
    ScenarioGateway,
    ScenarioSpec,
    ScenarioStep,
    ScenarioValidationError,
    validate_scenario,
)
from arrow_lake.actions.templates import (
    TemplateError,
    render_payload_item,
    render_template,
    validate_payload_item,
    validate_template,
)

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
    "ParsedPredicate",
    "ParsedPredicateError",
    "PostEvent",
    "ScenarioGateway",
    "ScenarioSpec",
    "ScenarioStep",
    "ScenarioValidationError",
    "TemplateError",
    "compile_predicate",
    "evaluate",
    "evaluate_expr",
    "render_payload_item",
    "render_template",
    "validate_payload_item",
    "validate_scenario",
    "validate_template",
]
