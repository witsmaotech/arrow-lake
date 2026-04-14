"""Arrow Lake base FlowSpec and flow registry (Story 6.1).

Provides the base class for all Arrow Lake Metaflow workflows and a
registry for discovering flows programmatically.
"""

from __future__ import annotations

from typing import ClassVar

import structlog

logger = structlog.get_logger(__name__)


class ArrowLakeFlowSpec:
    """Base mixin for all Arrow Lake Metaflow FlowSpecs.

    Inherit from both ``metaflow.FlowSpec`` and this class::

        from metaflow import FlowSpec, step
        from arrow_lake.workflow.base import ArrowLakeFlowSpec

        class MyFlow(ArrowLakeFlowSpec, FlowSpec):
            @step
            def start(self):
                ...

    Provides:
    - ``_load_config()``: Load ArrowLakeConfig from YAML or defaults.
    - ``_auto_tag()``: Log run metadata for tracking.
    """

    def _load_config(self) -> object:
        """Load ArrowLakeConfig from config_path parameter.

        Returns:
            ArrowLakeConfig instance.
        """
        from arrow_lake.config import ArrowLakeConfig

        config_path = getattr(self, "config_path", "")
        if config_path:
            return ArrowLakeConfig.from_yaml(config_path)
        return ArrowLakeConfig()

    def _auto_tag(self) -> None:
        """Log run metadata for tag-based tracking (Story 6.7).

        Attempts to access Metaflow ``current`` for run_id and flow_name.
        Falls back gracefully if Metaflow runtime is unavailable.
        """
        try:
            from metaflow import current  # type: ignore[import-untyped]

            logger.info(
                "workflow_run_started",
                flow=getattr(current, "flow_name", "unknown"),
                run_id=str(getattr(current, "run_id", "unknown")),
            )
        except ImportError:
            logger.info("workflow_run_started", flow="unknown", run_id="unknown")


class FlowRegistry:
    """Registry for discovering and managing Arrow Lake flows.

    Maps flow names to FlowSpec classes. Uses class-level storage
    (similar to QualityFilterRegistry pattern).

    Usage::

        from arrow_lake.workflow.base import FlowRegistry

        FlowRegistry.register("quality_pipeline", QualityPipelineFlow)
        FlowRegistry.list_flows()  # ["quality_pipeline"]
    """

    _flows: ClassVar[dict[str, type[ArrowLakeFlowSpec]]] = {}

    @classmethod
    def register(cls, name: str, flow_cls: type[ArrowLakeFlowSpec]) -> None:
        """Register a flow class under a name.

        Args:
            name: Flow name identifier.
            flow_cls: FlowSpec class to register.

        Raises:
            ValueError: If a flow with the same name is already registered.
        """
        if name in cls._flows:
            raise ValueError(f"Flow '{name}' is already registered")
        cls._flows[name] = flow_cls

    @classmethod
    def get(cls, name: str) -> type[ArrowLakeFlowSpec]:
        """Get a registered flow class by name.

        Args:
            name: Flow name identifier.

        Raises:
            KeyError: If the flow is not registered.
        """
        if name not in cls._flows:
            raise KeyError(f"Flow '{name}' is not registered")
        return cls._flows[name]

    @classmethod
    def list_flows(cls) -> list[str]:
        """List all registered flow names (sorted)."""
        return sorted(cls._flows.keys())

    @classmethod
    def clear(cls) -> None:
        """Remove all registered flows."""
        cls._flows.clear()
