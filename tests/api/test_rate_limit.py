"""Integration tests for rate limiting — M5."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from arrow_lake.config import ArrowLakeConfig, RateLimitConfig
from arrow_lake.api.app import create_app

SECRET = "test-secret-key-min-32-chars-for-hmac!"


@pytest.fixture
def rl_config() -> ArrowLakeConfig:
    """Create a config with rate limiting enabled for testing."""
    config = ArrowLakeConfig()
    config.auth.jwt_secret_key = SECRET
    config.rate_limit = RateLimitConfig(
        enabled=True,
        default_requests_per_minute=3,
        default_burst=2,
    )
    return config


@pytest.fixture
def rl_app(rl_config: ArrowLakeConfig):
    """Create a FastAPI app with rate limiting."""
    return create_app(config=rl_config)


@pytest.mark.asyncio
async def test_rate_limit_disabled_allows_requests() -> None:
    """When rate limiting is disabled, all requests should succeed."""
    config = ArrowLakeConfig()
    app = create_app(config=config)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for _ in range(10):
            resp = await ac.get("/health")
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_exempt_from_rate_limit(rl_app) -> None:
    """Health endpoints should bypass rate limiting even when enabled."""
    async with AsyncClient(transport=ASGITransport(app=rl_app), base_url="http://test") as ac:
        for _ in range(10):
            resp = await ac.get("/health/live")
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_metrics_exempt_from_rate_limit(rl_app) -> None:
    """Metrics endpoint should bypass rate limiting."""
    async with AsyncClient(transport=ASGITransport(app=rl_app), base_url="http://test") as ac:
        for _ in range(10):
            resp = await ac.get("/metrics")
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_config_accessible(rl_config: ArrowLakeConfig) -> None:
    """Rate limit config should be accessible."""
    assert rl_config.rate_limit.enabled is True
    assert rl_config.rate_limit.default_requests_per_minute == 3


@pytest.mark.asyncio
async def test_rate_limit_returns_429(rl_app) -> None:
    """After exceeding the rate limit, should return 429 with Retry-After header."""
    async with AsyncClient(transport=ASGITransport(app=rl_app), base_url="http://test") as ac:
        # Burn through the rate limit on a non-exempt endpoint
        for _ in range(3):
            resp = await ac.post("/api/v2/auth/token")
            assert resp.status_code == 200

        # Next request should be rate limited
        resp = await ac.post("/api/v2/auth/token")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        data = resp.json()
        assert data["error"] == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_rate_limit_headers_present(rl_app) -> None:
    """Rate limit headers should be present on successful responses."""
    async with AsyncClient(transport=ASGITransport(app=rl_app), base_url="http://test") as ac:
        resp = await ac.post("/api/v2/auth/token")
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
        assert resp.headers["X-RateLimit-Limit"] == "3"
