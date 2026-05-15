"""Tests for jwt_auth_middleware_fn — all bypass branches, auth failures, and success path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("jwt")

from arrow_lake.api.auth_models import Role
from arrow_lake.api.auth_service import AuthService
from arrow_lake.api.jwt_auth import jwt_auth_middleware_fn
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

SECRET = "test-secret-key-min-32-chars-for-hmac!"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_auth_service(**overrides) -> AuthService:
    defaults = dict(secret_key=SECRET, access_token_minutes=30, refresh_token_days=7)
    defaults.update(overrides)
    return AuthService(**defaults)


def _make_request(
    path: str = "/api/v1/datasets/test",
    method: str = "GET",
    headers: dict | None = None,
) -> MagicMock:
    """Build a mock Starlette Request for middleware tests."""
    request = MagicMock(spec=Request)
    request.url = MagicMock()
    request.url.path = path
    request.method = method
    request.headers = headers or {}
    request.state = MagicMock()
    return request


def _call_next_ok(request: MagicMock) -> AsyncMock:
    """Return a call_next that returns a 200 JSONResponse."""
    return AsyncMock(return_value=JSONResponse(status_code=200, content={"ok": True}))


# ===========================================================================
# Bypass: OPTIONS
# ===========================================================================


class TestOptionsBypass:
    """OPTIONS requests should bypass JWT auth entirely."""

    @pytest.mark.asyncio
    async def test_options_bypasses_auth(self) -> None:
        request = _make_request(path="/api/v1/datasets", method="OPTIONS")
        call_next = _call_next_ok(request)
        svc = _make_auth_service()

        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 200
        call_next.assert_called_once_with(request)


# ===========================================================================
# Bypass: Public paths
# ===========================================================================


class TestPublicPathBypass:
    """Health and metrics paths should bypass JWT auth."""

    @pytest.mark.asyncio
    async def test_health_bypasses(self) -> None:
        request = _make_request(path="/health/live")
        call_next = _call_next_ok(request)
        svc = _make_auth_service()

        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_bypasses(self) -> None:
        request = _make_request(path="/metrics")
        call_next = _call_next_ok(request)
        svc = _make_auth_service()

        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_prefix_bypasses(self) -> None:
        """Any /health/* path should bypass."""
        request = _make_request(path="/health/ready")
        call_next = _call_next_ok(request)
        svc = _make_auth_service()

        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 200


# ===========================================================================
# Bypass: Doc paths with docs_enabled
# ===========================================================================


class TestDocPathBypass:
    """Doc paths should bypass JWT auth only when docs_enabled=True."""

    @pytest.mark.asyncio
    async def test_docs_bypass_when_enabled(self) -> None:
        request = _make_request(path="/docs")
        call_next = _call_next_ok(request)
        svc = _make_auth_service()

        response = await jwt_auth_middleware_fn(
            request, call_next, auth_service=svc, docs_enabled=True,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_redoc_bypass_when_enabled(self) -> None:
        request = _make_request(path="/redoc")
        call_next = _call_next_ok(request)
        svc = _make_auth_service()

        response = await jwt_auth_middleware_fn(
            request, call_next, auth_service=svc, docs_enabled=True,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_openapi_json_bypass_when_enabled(self) -> None:
        request = _make_request(path="/openapi.json")
        call_next = _call_next_ok(request)
        svc = _make_auth_service()

        response = await jwt_auth_middleware_fn(
            request, call_next, auth_service=svc, docs_enabled=True,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_docs_blocked_when_disabled(self) -> None:
        """Doc paths should require auth when docs_enabled=False."""
        request = _make_request(path="/docs")
        call_next = AsyncMock()
        svc = _make_auth_service()

        response = await jwt_auth_middleware_fn(
            request, call_next, auth_service=svc, docs_enabled=False,
        )
        assert response.status_code == 401
        call_next.assert_not_called()


# ===========================================================================
# Bypass: Auth endpoints
# ===========================================================================


class TestAuthEndpointBypass:
    """Auth endpoints should bypass JWT auth."""

    @pytest.mark.asyncio
    async def test_auth_token_bypasses(self) -> None:
        request = _make_request(path="/api/v1/auth/token", method="POST")
        call_next = _call_next_ok(request)
        svc = _make_auth_service()

        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_auth_refresh_bypasses(self) -> None:
        request = _make_request(path="/api/v1/auth/refresh", method="POST")
        call_next = _call_next_ok(request)
        svc = _make_auth_service()

        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_auth_me_requires_jwt(self) -> None:
        request = _make_request(path="/api/v1/auth/me")
        call_next = _call_next_ok(request)
        svc = _make_auth_service()

        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 401


# ===========================================================================
# Auth failures
# ===========================================================================


class TestAuthFailures:
    """Tests for various authentication failure modes."""

    @pytest.mark.asyncio
    async def test_missing_authorization_header_returns_401(self) -> None:
        """No Authorization header should return AUTH_INVALID_TOKEN."""
        request = _make_request(path="/api/v1/datasets")
        call_next = AsyncMock()
        svc = _make_auth_service()

        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 401
        body = _decode_json_response(response)
        assert body["error"] == "AUTH_INVALID_TOKEN"
        assert "Missing" in body["message"]
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_bearer_scheme_returns_401(self) -> None:
        """Non-Bearer Authorization scheme should return AUTH_INVALID_TOKEN."""
        request = _make_request(
            path="/api/v1/datasets",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        call_next = AsyncMock()
        svc = _make_auth_service()

        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 401
        body = _decode_json_response(response)
        assert body["error"] == "AUTH_INVALID_TOKEN"
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_token_returns_401_with_expired_error(self) -> None:
        """Expired JWT should return AUTH_TOKEN_EXPIRED."""
        svc = _make_auth_service()
        past = datetime.now(UTC) - timedelta(hours=1)
        expired_payload = svc.create_access_token(user_id="user-1")
        # Manually set exp to the past.
        expired_payload.exp = past
        expired_payload.iat = past - timedelta(minutes=30)
        token = svc._encode(expired_payload)

        request = _make_request(
            path="/api/v1/datasets",
            headers={"Authorization": f"Bearer {token}"},
        )
        call_next = AsyncMock()
        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 401
        body = _decode_json_response(response)
        assert body["error"] == "AUTH_TOKEN_EXPIRED"
        assert "expired" in body["message"].lower()
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401_with_invalid_error(self) -> None:
        """Malformed JWT should return AUTH_INVALID_TOKEN."""
        svc = _make_auth_service()

        request = _make_request(
            path="/api/v1/datasets",
            headers={"Authorization": "Bearer not.a.real.token"},
        )
        call_next = AsyncMock()
        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 401
        body = _decode_json_response(response)
        assert body["error"] == "AUTH_INVALID_TOKEN"
        assert "invalid" in body["message"].lower() or "malformed" in body["message"].lower()
        call_next.assert_not_called()


# ===========================================================================
# Successful auth
# ===========================================================================


class TestSuccessfulAuth:
    """Tests for successful JWT verification."""

    @pytest.mark.asyncio
    async def test_valid_token_sets_user_and_passes_through(self) -> None:
        """Valid JWT should set request.state.user and call next middleware."""
        svc = _make_auth_service()
        payload = svc.create_access_token(user_id="user-42", role=Role.ADMIN, permissions=["write"])
        token = svc._encode(payload)

        request = _make_request(
            path="/api/v1/datasets",
            headers={"Authorization": f"Bearer {token}"},
        )
        expected_response = JSONResponse(status_code=200, content={"data": "ok"})
        call_next = AsyncMock(return_value=expected_response)

        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 200
        call_next.assert_called_once_with(request)
        # request.state.user should have been set with the payload.
        assert request.state.user is not None
        assert request.state.user.sub == "user-42"
        assert request.state.user.role == Role.ADMIN

    @pytest.mark.asyncio
    async def test_valid_token_viewer_role(self) -> None:
        """Valid token with viewer role should pass through."""
        svc = _make_auth_service()
        payload = svc.create_access_token(user_id="viewer-1", role=Role.VIEWER)
        token = svc._encode(payload)

        request = _make_request(
            path="/api/v1/datasets",
            headers={"Authorization": f"Bearer {token}"},
        )
        call_next = _call_next_ok(request)

        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 200
        assert request.state.user.sub == "viewer-1"
        assert request.state.user.role == Role.VIEWER


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _decode_json_response(response: Response) -> dict:
    """Decode a JSONResponse body to a dict."""
    import json

    if hasattr(response, "body"):
        return json.loads(response.body)
    return {}
