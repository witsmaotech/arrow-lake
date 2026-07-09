"""Unit tests for HugeGraphConfig."""

from __future__ import annotations

import pytest
from arrow_lake.config import ArrowLakeConfig, HugeGraphConfig
from pydantic import ValidationError


class TestHugeGraphConfig:
    """Tests for HugeGraphConfig defaults and validation."""

    def test_default_values(self) -> None:
        cfg = HugeGraphConfig()
        assert cfg.enabled is False
        assert cfg.host == "localhost"
        assert cfg.port == 8091
        assert cfg.graph_name == "hugegraph"
        assert cfg.timeout_seconds == 30.0
        assert cfg.username == ""
        assert cfg.password == ""
        assert cfg.auto_build_on_ingest is False
        assert cfg.build_batch_size == 50
        assert cfg.build_concurrency == 3
        assert cfg.write_concurrency == 2
        assert cfg.default_traversal_depth == 2
        assert cfg.max_traversal_depth == 5

    def test_enabled_true(self) -> None:
        cfg = HugeGraphConfig(enabled=True, host="hugegraph", port=9090)
        assert cfg.enabled is True
        assert cfg.host == "hugegraph"
        assert cfg.port == 9090

    def test_max_traversal_depth_valid(self) -> None:
        cfg = HugeGraphConfig(max_traversal_depth=3)
        assert cfg.max_traversal_depth == 3

    def test_max_traversal_depth_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="max_traversal_depth"):
            HugeGraphConfig(max_traversal_depth=0)

    def test_max_traversal_depth_exceeds_limit(self) -> None:
        with pytest.raises(ValidationError, match="max_traversal_depth"):
            HugeGraphConfig(max_traversal_depth=11)

    def test_build_batch_size_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="build_batch_size"):
            HugeGraphConfig(build_batch_size=0)

    def test_write_concurrency_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="write_concurrency"):
            HugeGraphConfig(write_concurrency=0)

    def test_write_concurrency_valid(self) -> None:
        cfg = HugeGraphConfig(write_concurrency=4)
        assert cfg.write_concurrency == 4

    def test_timeout_minimum(self) -> None:
        cfg = HugeGraphConfig(timeout_seconds=1.0)
        assert cfg.timeout_seconds == 1.0

    def test_timeout_below_minimum_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timeout_seconds"):
            HugeGraphConfig(timeout_seconds=0.5)


class TestHugeGraphConfigIntegration:
    """Tests for HugeGraphConfig integration into ArrowLakeConfig."""

    def test_arrow_lake_config_has_hugegraph(self) -> None:
        cfg = ArrowLakeConfig()
        assert hasattr(cfg, "hugegraph")
        assert isinstance(cfg.hugegraph, HugeGraphConfig)

    def test_hugegraph_in_section_types(self) -> None:
        from arrow_lake.config import _build_merged_update

        cfg = ArrowLakeConfig()
        merged = _build_merged_update(cfg, {"hugegraph": {"enabled": True}})
        assert "hugegraph" in merged
        assert merged["hugegraph"].enabled is True

    def test_env_var_hugegraph_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__HUGEGRAPH__ENABLED", "true")
        cfg = ArrowLakeConfig()
        assert cfg.hugegraph.enabled is True

    def test_env_var_hugegraph_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__HUGEGRAPH__HOST", "hugegraph-server")
        cfg = ArrowLakeConfig()
        assert cfg.hugegraph.host == "hugegraph-server"
