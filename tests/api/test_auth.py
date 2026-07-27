"""Tests for API Key authentication middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.api.auth import api_key_middleware_fn
from httpx import ASGITransport, AsyncClient


def _make_app_with_key(api_key: str) -> FastAPI:
    from arrow_lake.config import ArrowLakeConfig

    config = ArrowLakeConfig()
    config.api.enabled = True
    config.api.api_key = api_key
    config.auth.jwt_secret_key = "test-jwt-secret-key-at-least-32-characters-long"
    app = create_app(config=config)
    app.state.lake = MagicMock()
    # ASGITransport skips lifespan; mark ready so /health checks storage
    # instead of returning status="starting" (503).
    app.state.ready = True
    return app


def _mock_request(path: str = "/api/v1/datasets/test", method: str = "GET", headers: dict | None = None):
    """Build a mock Starlette Request."""
    request = MagicMock()
    request.url.path = path
    request.method = method
    request.headers.get = lambda k, d="": (headers or {}).get(k, d)
    request.state = MagicMock()
    request.app.state.identity_store = None  # 显式无 identity_store(避免 MagicMock 自动 mock 触发 token 分支)
    return request


class TestApiKeyMiddlewareFn:
    """Direct tests for api_key_middleware_fn (no app dependency)."""

    @pytest.mark.asyncio
    async def test_no_api_key_configured_rejects_protected(self):
        """When api_key is empty, protected endpoints return 401."""
        request = _mock_request(path="/api/v1/datasets/test/ingest")
        call_next = AsyncMock()

        result = await api_key_middleware_fn(
            request, call_next, api_key="", header_name="X-API-Key"
        )
        assert result.status_code == 401
        assert "not configured" in result.body.decode()

    @pytest.mark.asyncio
    async def test_valid_api_key_sets_user_and_proceeds(self):
        """Correct API key sets request.state.user and calls next."""
        request = _mock_request(headers={"X-API-Key": "secret-key"})
        call_next = AsyncMock(return_value=MagicMock())

        await api_key_middleware_fn(
            request, call_next, api_key="secret-key", header_name="X-API-Key"
        )
        call_next.assert_called_once_with(request)
        assert request.state.user is not None

    @pytest.mark.asyncio
    async def test_missing_api_key_rejected(self):
        """Missing API key on protected endpoint returns 401."""
        request = _mock_request()
        call_next = AsyncMock()

        result = await api_key_middleware_fn(
            request, call_next, api_key="secret-key", header_name="X-API-Key"
        )
        assert result.status_code == 401
        assert "API key" in result.body.decode()

    @pytest.mark.asyncio
    async def test_wrong_api_key_rejected(self):
        """Wrong API key returns 401."""
        request = _mock_request(headers={"X-API-Key": "wrong-key"})
        call_next = AsyncMock()

        result = await api_key_middleware_fn(
            request, call_next, api_key="secret-key", header_name="X-API-Key"
        )
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_public_paths_bypass_auth(self):
        """Public paths should be accessible without API key."""
        call_next = AsyncMock(return_value=MagicMock())

        # /health is public
        request = _mock_request(path="/health")
        await api_key_middleware_fn(request, call_next, api_key="secret-key")
        assert call_next.called

        # /health/live is public
        request = _mock_request(path="/health/live")
        call_next.reset_mock()
        await api_key_middleware_fn(request, call_next, api_key="secret-key")
        assert call_next.called

    @pytest.mark.asyncio
    async def test_options_bypasses_auth(self):
        """OPTIONS preflight requests bypass API key check."""
        request = _mock_request(method="OPTIONS")
        call_next = AsyncMock(return_value=MagicMock())

        await api_key_middleware_fn(
            request, call_next, api_key="secret-key", header_name="X-API-Key"
        )
        call_next.assert_called_once()


class TestApiKeyIntegration:
    """Integration tests through the full app."""

    @pytest.mark.asyncio
    async def test_valid_api_key_via_app(self):
        """A correct API key should work through the full app."""
        app = _make_app_with_key("secret-key")

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "secret-key"},
        ) as ac:
            resp = await ac.get("/health")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_public_paths_via_app(self):
        """Public paths should be accessible via the full app."""
        app = _make_app_with_key("secret-key")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/health")
            assert resp.status_code == 200
