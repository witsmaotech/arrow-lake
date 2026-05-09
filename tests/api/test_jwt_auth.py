"""Integration tests for JWT authentication endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytest.importorskip("jwt")

from datetime import UTC

from arrow_lake.api.app import create_app
from arrow_lake.api.auth_models import Role
from arrow_lake.api.auth_service import AuthService
from arrow_lake.config import ArrowLakeConfig

SECRET = "test-secret-key-min-32-chars-for-hmac!"


@pytest.fixture
def jwt_app() -> FastAPI:
    """Create app with JWT auth enabled."""
    config = ArrowLakeConfig()
    config.auth.auth_mode = "jwt"
    config.auth.jwt_secret_key = SECRET
    config.auth.jwt_bootstrap_token = "test-bootstrap-token"
    config.api.api_key = ""  # JWT-only mode: disable API key middleware
    app = create_app(config=config)
    app.state.lake = MagicMock()
    return app


@pytest.fixture
def both_app() -> FastAPI:
    """Create app with both API key + JWT auth."""
    config = ArrowLakeConfig()
    config.auth.auth_mode = "both"
    config.auth.jwt_secret_key = SECRET
    config.api.api_key = "my-api-key"
    app = create_app(config=config)
    app.state.lake = MagicMock()
    return app


@pytest.fixture
def jwt_svc() -> AuthService:
    return AuthService(secret_key=SECRET)


# ---------------------------------------------------------------------------
# Auth router endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exchange_token(jwt_app: FastAPI) -> None:
    """POST /api/v1/auth/token returns a token pair."""
    async with AsyncClient(
        transport=ASGITransport(app=jwt_app), base_url="http://test",
        headers={"Authorization": "Bearer test-bootstrap-token"},
    ) as ac:
        resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_refresh_token(jwt_app: FastAPI, jwt_svc: AuthService) -> None:
    """POST /api/v1/auth/refresh returns new tokens."""
    refresh = jwt_svc.create_refresh_token(user_id="user-1", role=Role.EDITOR)

    async with AsyncClient(transport=ASGITransport(app=jwt_app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body


@pytest.mark.asyncio
async def test_me_not_authenticated(jwt_app: FastAPI) -> None:
    """GET /api/v1/auth/me returns 401 without JWT."""
    async with AsyncClient(transport=ASGITransport(app=jwt_app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_valid_jwt(jwt_app: FastAPI, jwt_svc: AuthService) -> None:
    """GET /api/v1/auth/me returns user info with valid JWT."""
    payload = jwt_svc.create_access_token(user_id="user-1", role=Role.ADMIN)
    token = jwt_svc._encode(payload)

    async with AsyncClient(
        transport=ASGITransport(app=jwt_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        resp = await ac.get("/api/v1/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["sub"] == "user-1"
        assert body["role"] == "admin"


# ---------------------------------------------------------------------------
# JWT middleware — public paths bypass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jwt_health_bypass(jwt_app: FastAPI) -> None:
    """Health endpoints should bypass JWT auth."""
    async with AsyncClient(transport=ASGITransport(app=jwt_app), base_url="http://test") as ac:
        resp = await ac.get("/health/live")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_jwt_expired_token_rejected(jwt_app: FastAPI, jwt_svc: AuthService) -> None:
    """Expired JWT should be rejected."""
    from datetime import datetime, timedelta

    from arrow_lake.api.auth_models import TokenPayload

    past = datetime.now(UTC) - timedelta(hours=1)
    expired_payload = TokenPayload(
        sub="user-1", role=Role.VIEWER,
        exp=past, iat=past - timedelta(minutes=30),
    )
    expired_token = jwt_svc._encode(expired_payload)

    async with AsyncClient(
        transport=ASGITransport(app=jwt_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {expired_token}"},
    ) as ac:
        resp = await ac.get("/api/v1/auth/me")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_jwt_invalid_token_rejected(jwt_app: FastAPI) -> None:
    """Invalid JWT should be rejected."""
    async with AsyncClient(
        transport=ASGITransport(app=jwt_app),
        base_url="http://test",
        headers={"Authorization": "Bearer invalid.token.here"},
    ) as ac:
        resp = await ac.get("/api/v1/auth/me")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Both mode — API key grants JWT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_mode_token_exchange_with_api_key(both_app: FastAPI) -> None:
    """In 'both' mode, token exchange should work with API key."""
    async with AsyncClient(
        transport=ASGITransport(app=both_app),
        base_url="http://test",
        headers={"X-API-Key": "my-api-key"},
    ) as ac:
        resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code == 200
        assert "access_token" in resp.json()
