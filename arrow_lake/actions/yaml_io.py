"""Action/Scenario YAML 解析入口(v1.11.2 MS3 W2.3)。

capped safe load(节点/深度双帽,DoS 形输入 → ValueError)+ W1 模型校验,
供路由层 422 面。安全加载沿契约 ``_contract_yaml_load`` 同款构造
(SafeLoader 子类只改节点 COMPOSITION;``!!python/object`` 仍被拒)——
contract 模块零 diff 红线 → 本包内自持一份。
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError

from arrow_lake.actions.schema import ActionSpec, ScenarioSpec

__all__ = ["ActionYamlError", "parse_action_yaml", "parse_scenario_yaml"]

_MAX_NODES = 20_000
_MAX_DEPTH = 64


class ActionYamlError(ValueError):
    """YAML 不可解析/超帽/模型校验失败(路由层 → 422)。"""


class _CappedLoader(yaml.SafeLoader):
    """SafeLoader + 节点数/嵌套深度双帽(只覆盖 COMPOSITION,构造集不变)。"""

    _node_count: int = 0
    _depth: int = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self._node_count > _MAX_NODES:
            raise ValueError("YAML rejected: too many nodes")
        self._node_count += 1
        if self._depth > _MAX_DEPTH:
            raise ValueError("YAML rejected: nesting too deep")
        self._depth += 1
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def _capped_load(text: str) -> Any:
    _CappedLoader._node_count = 0
    _CappedLoader._depth = 0
    try:
        # 安全性:Loader 是 SafeLoader 子类且只覆盖 compose_node(计数/深度),
        # 构造集仍是 SafeLoader 的安全集 —— !!python/object 等标签照旧被拒
        # (与契约 _contract_yaml_load 同款已验证模式),非 unsafe_load。
        return yaml.load(text, Loader=_CappedLoader)
    except ValueError as exc:
        raise ActionYamlError(str(exc)) from exc
    except yaml.YAMLError as exc:
        raise ActionYamlError(f"YAML rejected: {exc}") from exc
    except RecursionError as exc:  # depth 帽之下的belt-and-braces
        raise ActionYamlError("YAML rejected: nesting too deep") from exc


def parse_action_yaml(text: str) -> ActionSpec:
    """YAML → ActionSpec;任何失败 → ActionYamlError(422 面)。"""
    raw = _capped_load(text)
    if not isinstance(raw, dict):
        raise ActionYamlError("action YAML must be a mapping")
    try:
        return ActionSpec.model_validate(raw)
    except ValidationError as exc:
        raise ActionYamlError(f"invalid action: {exc}") from exc


def parse_scenario_yaml(text: str) -> ScenarioSpec:
    """YAML → ScenarioSpec(引用完整性另走 validate_scenario)。"""
    raw = _capped_load(text)
    if not isinstance(raw, dict):
        raise ActionYamlError("scenario YAML must be a mapping")
    try:
        return ScenarioSpec.model_validate(raw)
    except ValidationError as exc:
        raise ActionYamlError(f"invalid scenario: {exc}") from exc
