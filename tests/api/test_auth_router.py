"""Tests for api/routers/auth.py — all auth-mode branches.

Covers: AuthMode.BOTH/JWT/API_KEY, token exchange, refresh, get_me, logout,
_get_auth_service fallback, _check_api_key edge cases, content-length 413,
missing JWT secret key 500.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from arrow_lake.api.app import create_app
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.api.auth_service import AuthService
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.config._enums import AuthMode

SECRET = "test-secret-key-min-32-chars-for-hmac!"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def jwt_svc() -> AuthService:
    return AuthService(secret_key=SECRET)


@pytest.fixture
def jwt_app() -> FastAPI:
    """App in JWT-only mode with bootstrap token."""
    config = ArrowLakeConfig()
    config.auth.auth_mode = AuthMode.JWT
    config.auth.jwt_secret_key = SECRET
    config.auth.jwt_bootstrap_token = "test-bootstrap-token"
    config.api.api_key = ""
    app = create_app(config=config)
    app.state.lake = MagicMock()
    return app


@pytest.fixture
def both_app() -> FastAPI:
    """App in BOTH mode (API key + JWT)."""
    config = ArrowLakeConfig()
    config.auth.auth_mode = AuthMode.BOTH
    config.auth.jwt_secret_key = SECRET
    config.api.api_key = "my-api-key"
    config.api.api_key_header = "X-API-Key"
    app = create_app(config=config)
    app.state.lake = MagicMock()
    return app


@pytest.fixture
def api_key_app() -> FastAPI:
    """App in API_KEY-only mode."""
    config = ArrowLakeConfig()
    config.auth.auth_mode = AuthMode.API_KEY
    config.auth.jwt_secret_key = SECRET
    config.api.api_key = "my-api-key"
    app = create_app(config=config)
    app.state.lake = MagicMock()
    return app




# ---------------------------------------------------------------------------
# JWT mode — token exchange
# ---------------------------------------------------------------------------


class TestJWTTokenExchange:
    """AuthMode.JWT: bootstrap token and refresh token exchange."""

    @pytest.mark.asyncio
    async def test_exchange_with_bootstrap_token(self, jwt_app: FastAPI) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=jwt_app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-bootstrap-token"},
        ) as ac:
            resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_exchange_with_refresh_token(
        self, jwt_app: FastAPI, jwt_svc: AuthService,
    ) -> None:
        refresh = jwt_svc.create_refresh_token(user_id="u1", role=Role.EDITOR)
        async with AsyncClient(
            transport=ASGITransport(app=jwt_app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {refresh}"},
        ) as ac:
            resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    @pytest.mark.asyncio
    async def test_exchange_rejects_invalid_bootstrap(self, jwt_app: FastAPI) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=jwt_app),
            base_url="http://test",
            headers={"Authorization": "Bearer wrong-token"},
        ) as ac:
            resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_exchange_rejects_no_auth_header(self, jwt_app: FastAPI) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=jwt_app),
            base_url="http://test",
        ) as ac:
            resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# BOTH mode — API key validation
# ---------------------------------------------------------------------------


class TestBothModeExchange:
    """AuthMode.BOTH: API key required for token exchange."""

    @pytest.mark.asyncio
    async def test_exchange_with_valid_api_key(self, both_app: FastAPI) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=both_app),
            base_url="http://test",
            headers={"X-API-Key": "my-api-key"},
        ) as ac:
            resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    @pytest.mark.asyncio
    async def test_exchange_rejects_wrong_api_key(self, both_app: FastAPI) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=both_app),
            base_url="http://test",
            headers={"X-API-Key": "wrong-key"},
        ) as ac:
            resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_exchange_rejects_missing_api_key(self, both_app: FastAPI) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=both_app),
            base_url="http://test",
        ) as ac:
            resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# API_KEY mode — allows token exchange with valid key
# ---------------------------------------------------------------------------


class TestApiKeyModeExchange:
    """AuthMode.API_KEY: token exchange with valid key; unauthenticated without."""

    @pytest.mark.asyncio
    async def test_exchange_with_valid_key(self, api_key_app: FastAPI) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api_key_app),
            base_url="http://test",
            headers={"X-API-Key": "my-api-key"},
        ) as ac:
            resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    @pytest.mark.asyncio
    async def test_exchange_rejects_wrong_key(self, api_key_app: FastAPI) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api_key_app),
            base_url="http://test",
            headers={"X-API-Key": "wrong"},
        ) as ac:
            resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# No JWT secret key — _validate_auth_config rejects at app creation
# ---------------------------------------------------------------------------


class TestNoSecretKey:
    """When jwt_secret_key is empty, create_app raises ValueError."""

    def test_create_app_rejects_empty_secret(self) -> None:
        config = ArrowLakeConfig()
        config.auth.auth_mode = AuthMode.JWT
        config.auth.jwt_secret_key = ""
        config.api.api_key = ""
        with pytest.raises(ValueError, match="jwt_secret_key"):
            create_app(config=config)


# ---------------------------------------------------------------------------
# Refresh token endpoint
# ---------------------------------------------------------------------------


class TestRefreshToken:
    """POST /api/v1/auth/refresh — validate refresh flow."""

    @pytest.mark.asyncio
    async def test_refresh_returns_new_pair(
        self, jwt_app: FastAPI, jwt_svc: AuthService,
    ) -> None:
        refresh = jwt_svc.create_refresh_token(user_id="u1", role=Role.EDITOR)
        async with AsyncClient(
            transport=ASGITransport(app=jwt_app),
            base_url="http://test",
        ) as ac:
            resp = await ac.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": refresh},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body

    @pytest.mark.asyncio
    async def test_refresh_rejects_missing_token(self, jwt_app: FastAPI) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=jwt_app),
            base_url="http://test",
        ) as ac:
            resp = await ac.post("/api/v1/auth/refresh", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_refresh_rejects_non_string_token(self, jwt_app: FastAPI) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=jwt_app),
            base_url="http://test",
        ) as ac:
            resp = await ac.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": 12345},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_refresh_rejects_expired_token(
        self, jwt_app: FastAPI, jwt_svc: AuthService,
    ) -> None:
        # Create an expired refresh token by patching
        from datetime import UTC, datetime, timedelta

        payload = jwt_svc.create_access_token(user_id="u1", role=Role.EDITOR)
        # Encode with past expiry — manually craft an expired token
        expired_payload = TokenPayload(
            sub="u1", role=Role.EDITOR,
            exp=datetime.now(UTC) - timedelta(days=1),
            iat=datetime.now(UTC) - timedelta(days=2),
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
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_rejects_oversized_body(self, jwt_app: FastAPI) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=jwt_app),
            base_url="http://test",
        ) as ac:
            # content-length > 10240 should trigger 413
            big_body = {"refresh_token": "x" * 11_000}
            resp = await ac.post("/api/v1/auth/refresh", json=big_body)
        assert resp.status_code == 413


# ---------------------------------------------------------------------------
# GET /me — current user info
# ---------------------------------------------------------------------------


class TestGetMe:
    """GET /api/v1/auth/me — user info from JWT."""

    @pytest.mark.asyncio
    async def test_me_with_valid_jwt(
        self, jwt_app: FastAPI, jwt_svc: AuthService,
    ) -> None:
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

    @pytest.mark.asyncio
    async def test_me_without_auth(self, jwt_app: FastAPI) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=jwt_app),
            base_url="http://test",
        ) as ac:
            resp = await ac.get("/api/v1/auth/me")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_me_with_expired_jwt(
        self, jwt_app: FastAPI, jwt_svc: AuthService,
    ) -> None:
        from datetime import UTC, datetime, timedelta

        expired_payload = TokenPayload(
            sub="user-1", role=Role.VIEWER,
            exp=datetime.now(UTC) - timedelta(hours=1),
            iat=datetime.now(UTC) - timedelta(minutes=90),
        )
        expired_token = jwt_svc._encode(expired_payload)
        async with AsyncClient(
            transport=ASGITransport(app=jwt_app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {expired_token}"},
        ) as ac:
            resp = await ac.get("/api/v1/auth/me")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /logout — token revocation
# ---------------------------------------------------------------------------


class TestLogout:
    """POST /api/v1/auth/logout — revoke JWT by jti."""

    @pytest.mark.asyncio
    async def test_logout_with_valid_jwt(
        self, jwt_app: FastAPI, jwt_svc: AuthService,
    ) -> None:
        payload = jwt_svc.create_access_token(user_id="u1", role=Role.EDITOR)
        token = jwt_svc._encode(payload)
        async with AsyncClient(
            transport=ASGITransport(app=jwt_app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as ac:
            resp = await ac.post("/api/v1/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Token revoked"

    @pytest.mark.asyncio
    async def test_logout_without_auth(self, jwt_app: FastAPI) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=jwt_app),
            base_url="http://test",
        ) as ac:
            resp = await ac.post("/api/v1/auth/logout")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# _get_auth_service fallback — no app.state.auth_service
# ---------------------------------------------------------------------------


class TestAuthServiceFallback:
    """_get_auth_service uses app.state.auth_service when set."""

    @pytest.mark.asyncio
    async def test_uses_existing_auth_service(self, jwt_app: FastAPI) -> None:
        """Token exchange uses the auth_service already on app.state."""
        assert hasattr(jwt_app.state, "auth_service")
        assert jwt_app.state.auth_service is not None
        async with AsyncClient(
            transport=ASGITransport(app=jwt_app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-bootstrap-token"},
        ) as ac:
            resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Both mode — no API key configured (open setup)
# ---------------------------------------------------------------------------


class TestBothModeNoApiKey:
    """AuthMode.BOTH with empty api_key should allow exchange."""

    @pytest.mark.asyncio
    async def test_allows_without_api_key_when_empty(self) -> None:
        config = ArrowLakeConfig()
        config.auth.auth_mode = AuthMode.BOTH
        config.auth.jwt_secret_key = SECRET
        config.api.api_key = ""
        app = create_app(config=config)
        app.state.lake = MagicMock()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            resp = await ac.post("/api/v1/auth/token")
        assert resp.status_code == 200
