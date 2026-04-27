"""Unit tests for security response headers middleware."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

pytest.importorskip("jwt")

from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig


def _app_with_security(**overrides) -> ArrowLakeConfig:
    config = ArrowLakeConfig()
    for key, value in overrides.items():
        setattr(config.api, key, value)
    return config


@pytest.mark.asyncio
async def test_security_headers_present_on_api_routes() -> None:
    """API routes should include security headers by default."""
    config = ArrowLakeConfig()
    config.auth.jwt_secret_key = "test-secret-key-must-be-32-chars!!"
    app = create_app(config=config)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code == 200
        assert "x-content-type-options" in resp.headers
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert "referrer-policy" in resp.headers
        assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_frame_options_default() -> None:
    """X-Frame-Options should default to DENY."""
    config = ArrowLakeConfig()
    app = create_app(config=config)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/auth/token")
        assert "x-frame-options" in resp.headers
        assert resp.headers["x-frame-options"] == "DENY"


@pytest.mark.asyncio
async def test_frame_options_sameorigin() -> None:
    """X-Frame-Options can be configured to SAMEORIGIN."""
    config = _app_with_security(frame_options="SAMEORIGIN")
    app = create_app(config=config)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/auth/token")
        assert resp.headers["x-frame-options"] == "SAMEORIGIN"


@pytest.mark.asyncio
async def test_csp_custom() -> None:
    """Custom Content-Security-Policy should be set when configured."""
    csp = "default-src 'self'; script-src 'self' 'nonce-abc123'"
    config = _app_with_security(content_security_policy=csp)
    app = create_app(config=config)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/auth/token")
        assert "content-security-policy" in resp.headers
        assert resp.headers["content-security-policy"] == csp


@pytest.mark.asyncio
async def test_csp_empty_not_set() -> None:
    """CSP header should not be set when empty (default)."""
    config = ArrowLakeConfig()
    assert config.api.content_security_policy == ""
    app = create_app(config=config)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/auth/token")
        assert "content-security-policy" not in resp.headers


@pytest.mark.asyncio
async def test_security_headers_disabled() -> None:
    """When security_headers_enabled is False, no security headers."""
    config = _app_with_security(security_headers_enabled=False)
    app = create_app(config=config)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/auth/token")
        assert "x-content-type-options" not in resp.headers
        assert "referrer-policy" not in resp.headers


@pytest.mark.asyncio
async def test_health_skips_security_headers() -> None:
    """Health endpoints should not get security headers."""
    config = ArrowLakeConfig()
    app = create_app(config=config)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        assert "x-content-type-options" not in resp.headers
        assert "referrer-policy" not in resp.headers


@pytest.mark.asyncio
async def test_metrics_skips_security_headers() -> None:
    """Metrics endpoint should not get security headers."""
    config = ArrowLakeConfig()
    app = create_app(config=config)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/metrics")
        assert resp.status_code == 200
        assert "x-content-type-options" not in resp.headers
