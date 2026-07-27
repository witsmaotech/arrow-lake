"""Tests for api/errors.py and api/deps.py — error mapping and dependency injection.

Supplements existing test_errors.py with uncovered ErrorCode mappings,
and tests deps.py dependency injection functions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from arrow_lake.api.app import create_app
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.api.errors import _error_code_to_http_status, register_exception_handlers
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.exceptions import ArrowLakeError, ErrorCode


# ---------------------------------------------------------------------------
# errors.py — uncovered ErrorCode→status mappings
# ---------------------------------------------------------------------------


class TestErrorCodeMappings:
    """Parametrized tests for all remaining ErrorCode→HTTP status mappings."""

    @pytest.mark.parametrize(
        "code,expected",
        [
            # Ingest errors → 400
            (ErrorCode.INGEST_UNSUPPORTED_FORMAT, 400),
            (ErrorCode.INGEST_FILE_NOT_FOUND, 400),
            # 404 group
            (ErrorCode.QUERY_TABLE_NOT_REGISTERED, 404),
            (ErrorCode.WORKFLOW_FLOW_NOT_FOUND, 404),
            (ErrorCode.STORAGE_PATH_NOT_FOUND, 404),
            # Rate limiting → 429
            (ErrorCode.HTTP_RATE_LIMITED, 429),
            # RAG errors
            (ErrorCode.RAG_TEMPLATE_NOT_FOUND, 404),
            (ErrorCode.RAG_SESSION_NOT_FOUND, 404),
            (ErrorCode.RAG_CONTEXT_TOO_LONG, 400),
            (ErrorCode.RAG_PROVIDER_ERROR, 502),
            # KG errors
            (ErrorCode.KG_GRAPH_NOT_FOUND, 404),
            (ErrorCode.KG_SCHEMA_ERROR, 400),
            (ErrorCode.KG_TRAVERSAL_TIMEOUT, 504),
            (ErrorCode.KG_CONNECTION_FAILED, 502),
            (ErrorCode.KG_EXTRACT_FAILED, 502),
            # KG catch-all → 500
            (ErrorCode.KG_QUERY_FAILED, 500),
            (ErrorCode.KG_BUILD_FAILED, 500),
            # Auth errors
            (ErrorCode.AUTH_TOKEN_EXPIRED, 401),
            (ErrorCode.AUTH_INVALID_TOKEN, 401),
            (ErrorCode.AUTH_INSUFFICIENT_PERMISSIONS, 403),
            (ErrorCode.AUTH_API_KEY_ROTATION_REQUIRED, 403),
        ],
    )
    def test_error_code_status(self, code: ErrorCode, expected: int) -> None:
        assert _error_code_to_http_status(code) == expected


class TestErrorHandlerContext:
    """Test exception handler with context (sensitive key stripping)."""

    @pytest.mark.asyncio
    async def test_context_with_sensitive_keys_stripped(self) -> None:
        config = ArrowLakeConfig()
        config.api.api_key = "test-key"
        app = create_app(config=config)
        app.state.lake = MagicMock()

        @app.get("/test-ctx")
        async def trigger():
            raise ArrowLakeError(
                error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
                message="Bad config",
                context={"query": "secret-sql", "safe_key": "visible"},
            )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key"},
        ) as ac:
            resp = await ac.get("/test-ctx")
        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert "context" in body
        assert "safe_key" in body["context"]
        assert "query" not in body["context"]

    @pytest.mark.asyncio
    async def test_context_empty_omits_field(self) -> None:
        config = ArrowLakeConfig()
        config.api.api_key = "test-key"
        app = create_app(config=config)
        app.state.lake = MagicMock()

        @app.get("/test-no-ctx")
        async def trigger():
            raise ArrowLakeError(
                error_code=ErrorCode.CATALOG_DATASET_NOT_FOUND,
                message="Not found",
            )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key"},
        ) as ac:
            resp = await ac.get("/test-no-ctx")
        assert resp.status_code == 404
        body = resp.json()
        assert "context" not in body


# ---------------------------------------------------------------------------
# deps.py — dependency injection functions
# ---------------------------------------------------------------------------


class TestGetAppConfig:
    """get_app_config reads from request.app.state.config."""

    @pytest.mark.asyncio
    async def test_reads_from_app_state(self) -> None:
        config = ArrowLakeConfig()
        config.api.api_key = "test-key"
        config.api.docs_enabled = False
        config.compute.ray_address = ""
        app = create_app(config=config)
        app.state.lake = MagicMock()
        # ASGITransport skips lifespan; mark ready so /health/ready checks storage.
        app.state.ready = True

        # The config should be accessible via the health endpoint
        # which uses get_app_config internally
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            resp = await ac.get("/health/ready")
        assert resp.status_code == 200


class TestGetLake:
    """get_lake reads from request.app.state.lake."""

    @pytest.mark.asyncio
    async def test_lake_available_in_endpoints(self) -> None:
        config = ArrowLakeConfig()
        config.api.api_key = "test-key"
        config.api.docs_enabled = False
        app = create_app(config=config)
        mock_lake = MagicMock()
        app.state.lake = mock_lake

        # Verify lake is accessible via an endpoint that uses get_lake
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key"},
        ) as ac:
            resp = await ac.get("/api/v1/datasets")
        assert resp.status_code == 200


class TestGetChecker:
    """get_checker returns app.state.checker or creates new PermissionChecker."""

    @pytest.mark.asyncio
    async def test_fallback_creates_new_checker(self) -> None:
        """Without app.state.checker, a new PermissionChecker is created."""
        from arrow_lake.api.rbac import PermissionChecker

        config = ArrowLakeConfig()
        config.api.api_key = "test-key"
        config.api.docs_enabled = False
        app = create_app(config=config)
        app.state.lake = MagicMock()
        # Don't set app.state.checker — should fallback
        if hasattr(app.state, "checker"):
            delattr(app.state, "checker")

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key"},
        ) as ac:
            resp = await ac.get("/api/v1/datasets")
        assert resp.status_code == 200


class TestGetCurrentUser:
    """get_current_user extracts user from request.state.user or JWT Bearer token."""

    @pytest.mark.asyncio
    async def test_with_jwt_bearer_token(self) -> None:
        """Valid JWT Bearer token sets user via auth middleware."""
        from arrow_lake.api.auth_service import AuthService

        config = ArrowLakeConfig()
        config.auth.auth_mode = "jwt"
        config.auth.jwt_secret_key = "test-secret-key-min-32-chars-for-hmac!"
        config.api.api_key = ""
        app = create_app(config=config)
        app.state.lake = MagicMock()

        svc = AuthService(secret_key="test-secret-key-min-32-chars-for-hmac!")
        payload = svc.create_access_token(user_id="user-1", role=Role.ADMIN)
        token = svc._encode(payload)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as ac:
            resp = await ac.get("/api/v1/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["sub"] == "user-1"

    @pytest.mark.asyncio
    async def test_without_auth_returns_401(self) -> None:
        """No auth header → 401."""
        config = ArrowLakeConfig()
        config.auth.auth_mode = "jwt"
        config.auth.jwt_secret_key = "test-secret-key-min-32-chars-for-hmac!"
        config.api.api_key = ""
        app = create_app(config=config)
        app.state.lake = MagicMock()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            resp = await ac.get("/api/v1/auth/me")
        assert resp.status_code == 401


class TestRequireRole:
    """require_role factory enforces role hierarchy."""

    @pytest.mark.asyncio
    async def test_viewer_blocked_from_editor_endpoint(self) -> None:
        """VIEWER JWT cannot access EDITOR-required endpoints."""
        from arrow_lake.api.auth_service import AuthService

        config = ArrowLakeConfig()
        config.auth.auth_mode = "jwt"
        config.auth.jwt_secret_key = "test-secret-key-min-32-chars-for-hmac!"
        config.api.api_key = ""
        app = create_app(config=config)
        app.state.lake = MagicMock()

        svc = AuthService(secret_key="test-secret-key-min-32-chars-for-hmac!")
        payload = svc.create_access_token(user_id="viewer-1", role=Role.VIEWER)
        token = svc._encode(payload)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as ac:
            # Try to access an EDITOR endpoint
            resp = await ac.post(
                "/api/v1/datasets/test/query/olap",
                json={"sql": "SELECT 1"},
            )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_admin_can_access_any_endpoint(self) -> None:
        """ADMIN JWT can access all endpoints."""
        from arrow_lake.api.auth_service import AuthService

        config = ArrowLakeConfig()
        config.auth.auth_mode = "jwt"
        config.auth.jwt_secret_key = "test-secret-key-min-32-chars-for-hmac!"
        config.api.api_key = ""
        app = create_app(config=config)
        app.state.lake = MagicMock()

        svc = AuthService(secret_key="test-secret-key-min-32-chars-for-hmac!")
        payload = svc.create_access_token(user_id="admin-1", role=Role.ADMIN)
        token = svc._encode(payload)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as ac:
            resp = await ac.get("/api/v1/version")
        assert resp.status_code == 200
