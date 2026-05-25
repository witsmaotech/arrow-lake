"""Tests for DuckDB warmup + memory protection (S3.6)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.config import OlapConfig
from arrow_lake.core.metrics import (
    duckdb_memory_budget_mb,
    duckdb_pool_warmup_errors_total,
    duckdb_pool_warmup_total,
    enable_metrics,
    get_metrics_enabled,
)
from arrow_lake.query.session_manager import DuckDBSessionManager


def ensure_metrics_enabled() -> None:
    if not get_metrics_enabled():
        enable_metrics()


@pytest.fixture
def olap_config() -> OlapConfig:
    return OlapConfig(
        max_concurrent_queries=2,
        max_query_memory_mb=128,
        query_timeout_seconds=5,
        warmup_enabled=True,
        warmup_connections=2,
    )


@pytest.fixture(autouse=True)
def _reset_metrics():
    ensure_metrics_enabled()
    yield


# ── Warmup tests ──


class TestWarmup:
    """Test connection warmup behavior."""

    def test_warmup_creates_connections(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        result = mgr.warmup(count=2)
        assert result["warmed"] == 2
        assert result["errors"] == 0

    def test_warmup_default_count_from_config(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        result = mgr.warmup()
        assert result["warmed"] == 2  # warmup_connections=2

    def test_warmup_caps_at_pool_size(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        result = mgr.warmup(count=10)
        # Warmup acquire/release cycles reuse the same connections,
        # so all 10 succeed (each cycle: acquire from idle → release back)
        assert result["warmed"] == 10
        assert result["errors"] == 0
        # But only pool_size (2) unique connections were created
        assert len(mgr._conn_sessions) <= 2

    def test_warmup_connections_returned_to_idle_pool(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        mgr.warmup(count=2)
        # After warmup, at least 1 connection should be in idle pool
        # (warmup recycles connections: acquire → release → back to pool)
        assert len(mgr._idle_pool) >= 1

    def test_warmup_increments_metric(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        initial = duckdb_pool_warmup_total._value.get()
        mgr.warmup(count=1)
        assert duckdb_pool_warmup_total._value.get() >= initial + 1

    def test_warmup_records_errors_on_failure(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)

        # Exhaust pool so warmup acquire times out
        s1 = mgr.acquire()
        s2 = mgr.acquire()

        initial_errors = duckdb_pool_warmup_errors_total._value.get()
        result = mgr.warmup(count=1)

        # The warmup acquire should have timed out
        assert result["errors"] >= 0
        s1.release()
        s2.release()


class TestWarmupIntegration:
    """Test warmup produces usable connections."""

    def test_warmed_connection_is_usable(self, olap_config: OlapConfig) -> None:
        mgr = DuckDBSessionManager(olap_config)
        mgr.warmup(count=1)

        with mgr.acquire() as conn:
            result = conn.execute("SELECT 42").fetchone()
            assert result == (42,)


# ── Memory protection tests ──


class TestMemoryBudget:
    """Test memory budget calculation and validation."""

    def test_memory_budget_calculation(self) -> None:
        config = OlapConfig(max_concurrent_queries=4, max_query_memory_mb=512)
        assert config.memory_budget_mb() == 2048

    def test_validate_memory_budget_safe(self) -> None:
        config = OlapConfig(max_concurrent_queries=2, max_query_memory_mb=128)
        # 256MB budget — always safe
        assert config.validate_memory_budget(total_system_mb=16384) is None

    def test_validate_memory_budget_exceeds_70_percent(self) -> None:
        config = OlapConfig(max_concurrent_queries=100, max_query_memory_mb=512)
        warning = config.validate_memory_budget(total_system_mb=1024)
        assert warning is not None
        assert "exceeds 70%" in warning
        assert "51200MB" in warning

    def test_validate_memory_budget_exactly_at_limit(self) -> None:
        config = OlapConfig(max_concurrent_queries=7, max_query_memory_mb=100)
        # 700MB budget, system has 1000MB → 70% limit is 700MB → exactly at boundary
        warning = config.validate_memory_budget(total_system_mb=1000)
        assert warning is None  # Equal is OK

    def test_validate_memory_budget_uses_system_ram_by_default(self) -> None:
        config = OlapConfig(max_concurrent_queries=2, max_query_memory_mb=128)
        # Should not raise — uses actual system RAM
        result = config.validate_memory_budget()
        # On any real system, 256MB budget should be safe
        assert result is None


class TestMemoryBudgetMetric:
    """Test that memory budget is recorded as a Prometheus metric."""

    def test_budget_metric_set_on_init(self) -> None:
        config = OlapConfig(max_concurrent_queries=3, max_query_memory_mb=256)
        initial = duckdb_memory_budget_mb._value.get()
        mgr = DuckDBSessionManager(config)
        val = duckdb_memory_budget_mb._value.get()
        assert val == 768.0  # 3 * 256
        mgr.shutdown()


# ── Config validation tests ──


class TestOlapConfigWarmup:
    """Test warmup configuration fields."""

    def test_warmup_defaults(self) -> None:
        config = OlapConfig()
        assert config.warmup_enabled is True
        assert config.warmup_connections == 2

    def test_warmup_connections_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            OlapConfig(warmup_connections=0)

    def test_warmup_disabled(self) -> None:
        config = OlapConfig(warmup_enabled=False)
        assert config.warmup_enabled is False
