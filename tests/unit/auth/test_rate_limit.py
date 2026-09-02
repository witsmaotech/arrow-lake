"""Tests for rate limiting configuration and middleware — M5."""

from __future__ import annotations

import pytest
from arrow_lake.config import ArrowLakeConfig, RateLimitConfig


class TestRateLimitConfig:
    """Test RateLimitConfig defaults and validation."""

    def test_default_values(self) -> None:
        config = RateLimitConfig()
        assert config.enabled is True
        assert config.default_requests_per_minute == 60
        assert config.default_burst == 10
        # override_per_endpoint was removed in the v1.10.x 配置精简 (dead field).
        assert len(config.exempt_paths) > 0

    def test_enabled_true(self) -> None:
        config = RateLimitConfig(enabled=True)
        assert config.enabled is True

    def test_custom_limits(self) -> None:
        config = RateLimitConfig(
            default_requests_per_minute=100,
            default_burst=20,
        )
        assert config.default_requests_per_minute == 100
        assert config.default_burst == 20

    def test_config_integration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """RateLimitConfig is accessible via ArrowLakeConfig."""
        # The suite pins RATE_LIMIT__ENABLED=false (tests/conftest.py hermetic
        # anchor); scrub it to assert the class default.
        monkeypatch.delenv("ARROW_LAKE__RATE_LIMIT__ENABLED", raising=False)
        config = ArrowLakeConfig()
        assert hasattr(config, "rate_limit")
        assert isinstance(config.rate_limit, RateLimitConfig)
        assert config.rate_limit.enabled is True

    def test_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rate limit config can be set via environment variables."""
        monkeypatch.setenv("ARROW_LAKE__RATE_LIMIT__ENABLED", "true")
        monkeypatch.setenv("ARROW_LAKE__RATE_LIMIT__DEFAULT_REQUESTS_PER_MINUTE", "120")
        config = ArrowLakeConfig()
        assert config.rate_limit.enabled is True
        assert config.rate_limit.default_requests_per_minute == 120


class TestGetLimiter:
    """Test rate limiter factory function."""

    def test_returns_false_when_disabled(self) -> None:
        from arrow_lake.api.rate_limit import get_limiter

        result = get_limiter(RateLimitConfig(enabled=False))
        assert result is False

    def test_returns_true_when_enabled(self) -> None:
        from arrow_lake.api.rate_limit import get_limiter

        config = RateLimitConfig(enabled=True, default_requests_per_minute=100)
        result = get_limiter(config)
        assert result is True
