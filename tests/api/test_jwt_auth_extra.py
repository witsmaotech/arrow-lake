"""Extra integration tests for JWT authentication — refresh token edge cases and OPTIONS bypass."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytest.importorskip("jwt")

from arrow_lake.api.app import create_app
from arrow_lake.api.auth_service import AuthService
from arrow_lake.api.auth_models import Role
from arrow_lake.config import ArrowLakeConfig

SECRET = "test-secret-key-min-32-chars-for-hmac!"


@pytest.fixture
def jwt_app() -> FastAPI:
    """Create app with JWT auth enabled."""
    config = ArrowLakeConfig()
    config.auth.auth_mode = "jwt"
    config.auth.jwt_secret_key = SECRET
    app = create_app(config=config)
    app.state.lake = MagicMock()
    return app


@pytest.fixture
def jwt_svc() -> AuthService:
    return AuthService(secret_key=SECRET)


# ---------------------------------------------------------------------------
# 1. Expired refresh token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_refresh_token_rejected(jwt_app: FastAPI, jwt_svc: AuthService) -> None:
    """POST /api/v1/auth/refresh with an expired refresh token should return 4xx."""
    from datetime import datetime, timedelta, timezone
    from arrow_lake.api.auth_models import TokenPayload

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expired_payload = TokenPayload(
        sub="user-1",
        role=Role.EDITOR,
        exp=past,
        iat=past - timedelta(minutes=30),
    )
    expired_token = jwt_svc._encode(expired_payload)

    async with AsyncClient(
        transport=ASGITransport(app=jwt_app),
        base_url="http://test",
    ) as ac:
        resp = await ac.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": expired_token},
        )
        assert resp.status_code in (400, 401)


# ---------------------------------------------------------------------------
# 2. Malformed refresh token body — missing key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_with_missing_token_key(jwt_app: FastAPI) -> None:
    """POST /api/v1/auth/refresh with {} (no refresh_token) should return 400."""
    async with AsyncClient(
        transport=ASGITransport(app=jwt_app),
        base_url="http://test",
    ) as ac:
        resp = await ac.post(
            "/api/v1/auth/refresh",
            json={},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "refresh_token" in str(body).lower()


# ---------------------------------------------------------------------------
# 3. Refresh token is not a string
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token_must_be_string(jwt_app: FastAPI) -> None:
    """POST /api/v1/auth/refresh with a non-string refresh_token should return 400."""
    async with AsyncClient(
        transport=ASGITransport(app=jwt_app),
        base_url="http://test",
    ) as ac:
        resp = await ac.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": 12345},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 4. OPTIONS preflight bypass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_options_preflight_bypasses_jwt(jwt_app: FastAPI) -> None:
    """OPTIONS requests to protected endpoints should bypass JWT auth."""
    async with AsyncClient(
        transport=ASGITransport(app=jwt_app),
        base_url="http://test",
    ) as ac:
        # /api/v1/auth/me is a protected endpoint that normally requires JWT.
        resp = await ac.options("/api/v1/auth/me")
        # OPTIONS should not return 401 — either 200 or a method-not-allowed.
        assert resp.status_code != 401


@pytest.mark.asyncio
async def test_options_preflight_bypasses_jwt_on_datasets(jwt_app: FastAPI) -> None:
    """OPTIONS preflight on another protected route should also bypass JWT."""
    async with AsyncClient(
        transport=ASGITransport(app=jwt_app),
        base_url="http://test",
    ) as ac:
        resp = await ac.options("/api/v1/datasets")
        assert resp.status_code != 401


# ---------------------------------------------------------------------------
# 5. Invalid (garbage) refresh token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_refresh_token_rejected(jwt_app: FastAPI) -> None:
    """POST /api/v1/auth/refresh with a completely invalid token string."""
    async with AsyncClient(
        transport=ASGITransport(app=jwt_app),
        base_url="http://test",
    ) as ac:
        resp = await ac.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "this-is-not-a-valid-jwt-token"},
        )
        assert resp.status_code in (400, 401)


# ---------------------------------------------------------------------------
# 6. Refresh body is not a dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_with_non_dict_body(jwt_app: FastAPI) -> None:
    """POST /api/v1/auth/refresh with a list body should fail gracefully."""
    async with AsyncClient(
        transport=ASGITransport(app=jwt_app),
        base_url="http://test",
    ) as ac:
        resp = await ac.post(
            "/api/v1/auth/refresh",
            json=[1, 2, 3],
        )
        assert resp.status_code in (400, 422)
