"""Tests for Story 6.1 — ArrowLakeFlowSpec + FlowRegistry."""

from __future__ import annotations

import pytest
from arrow_lake.workflow.base import ArrowLakeFlowSpec, FlowRegistry


class MockFlow(ArrowLakeFlowSpec):
    """Mock flow for testing registry."""


class AnotherFlow(ArrowLakeFlowSpec):
    """Another mock flow for testing."""


class TestFlowRegistry:
    """Test FlowRegistry class methods."""

    def setup_method(self) -> None:
        FlowRegistry.clear()

    def teardown_method(self) -> None:
        FlowRegistry.clear()

    def test_register_and_list(self) -> None:
        FlowRegistry.register("test_flow", MockFlow)
        assert FlowRegistry.list_flows() == ["test_flow"]

    def test_register_duplicate_raises(self) -> None:
        FlowRegistry.register("dup", MockFlow)
        with pytest.raises(ValueError, match="already registered"):
            FlowRegistry.register("dup", AnotherFlow)

    def test_get_registered_flow(self) -> None:
        FlowRegistry.register("my_flow", MockFlow)
        assert FlowRegistry.get("my_flow") is MockFlow

    def test_get_missing_raises(self) -> None:
        with pytest.raises(KeyError, match="not registered"):
            FlowRegistry.get("nonexistent")

    def test_clear(self) -> None:
        FlowRegistry.register("a", MockFlow)
        FlowRegistry.register("b", AnotherFlow)
        assert len(FlowRegistry.list_flows()) == 2
        FlowRegistry.clear()
        assert FlowRegistry.list_flows() == []

    def test_list_flows_sorted(self) -> None:
        FlowRegistry.register("charlie", MockFlow)
        FlowRegistry.register("alpha", AnotherFlow)
        FlowRegistry.register("bravo", MockFlow)
        assert FlowRegistry.list_flows() == ["alpha", "bravo", "charlie"]

    def test_register_multiple_flows(self) -> None:
        FlowRegistry.register("flow_a", MockFlow)
        FlowRegistry.register("flow_b", AnotherFlow)
        assert len(FlowRegistry.list_flows()) == 2


class TestArrowLakeFlowSpec:
    """Test ArrowLakeFlowSpec mixin methods."""

    def test_load_config_defaults(self) -> None:
        flow = MockFlow()
        flow.config_path = ""
        config = flow._load_config()
        assert config.workflow.max_retry_attempts == 3

    def test_load_config_from_yaml(self, tmp_path: object) -> None:
        import pathlib

        p = pathlib.Path(str(tmp_path)) / "config.yaml"
        p.write_text("workflow:\n  max_retry_attempts: 5\n")
        flow = MockFlow()
        flow.config_path = str(p)
        config = flow._load_config()
        assert config.workflow.max_retry_attempts == 5

    def test_auto_tag(self) -> None:
        flow = MockFlow()
        flow._auto_tag()  # Should not raise
