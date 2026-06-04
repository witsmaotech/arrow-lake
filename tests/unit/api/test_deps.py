"""Tests for api/deps.py — dependency injection functions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.api.deps import (
    get_app_config,
    get_checker,
    get_current_user,
    get_lake,
    require_role,
)


def _mock_request(**state_overrides):
    """Build a mock FastAPI Request.

    Sets up realistic attribute access: getattr with defaults
    returns None for unset attrs.
    """
    from types import SimpleNamespace

    request = MagicMock()
    request.app.state = SimpleNamespace(
        config=MagicMock(),
        lake=MagicMock(),
    )
    request.state = SimpleNamespace(**state_overrides)
    request.headers = {}
    return request


# ===========================================================================
# get_app_config
# ===========================================================================


class TestGetAppConfig:
    def test_returns_config_from_state(self) -> None:
        req = _mock_request()
        config = get_app_config(req)
        assert config is req.app.state.config


# ===========================================================================
# get_lake
# ===========================================================================


class TestGetLake:
    def test_returns_lake_from_state(self) -> None:
        req = _mock_request()
        lake = get_lake(req)
        assert lake is req.app.state.lake


# ===========================================================================
# get_checker
# ===========================================================================


class TestGetChecker:
    def test_returns_checker_from_state(self) -> None:
        req = _mock_request()
        checker = MagicMock()
        req.app.state.checker = checker
        assert get_checker(req) is checker

    def test_fallback_creates_new(self) -> None:
        req = _mock_request()
        # checker not in SimpleNamespace → getattr returns None
        result = get_checker(req)
        assert result is not None  # Should create a new PermissionChecker


# ===========================================================================
# get_current_user
# ===========================================================================


class TestGetCurrentUser:
    def test_returns_user_from_state(self) -> None:
        user = TokenPayload(sub="u1", role=Role.VIEWER, exp=0, iat=0)
        req = _mock_request(user=user)
        assert get_current_user(req) is user

    def test_raises_401_without_bearer(self) -> None:
        req = _mock_request()
        req.headers = {"Authorization": "Basic abc"}
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(req)
        assert exc_info.value.status_code == 401

    def test_raises_401_no_auth_header(self) -> None:
        req = _mock_request()
        req.headers = {}
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(req)
        assert exc_info.value.status_code == 401

    def test_raises_401_no_auth_service(self) -> None:
        req = _mock_request()
        req.headers = {"Authorization": "Bearer token123"}
        # auth_service not set on SimpleNamespace → getattr returns None
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(req)
        assert exc_info.value.status_code == 401

    def test_verifies_token_via_auth_service(self) -> None:
        req = _mock_request()
        req.headers = {"Authorization": "Bearer valid-token"}
        user = TokenPayload(sub="u1", role=Role.ADMIN, exp=0, iat=0)
        svc = MagicMock()
        svc.verify_token.return_value = user
        req.app.state.auth_service = svc
        result = get_current_user(req)
        assert result == user
        svc.verify_token.assert_called_once_with("valid-token")

    def test_raises_401_on_invalid_token(self) -> None:
        req = _mock_request()
        req.headers = {"Authorization": "Bearer bad-token"}
        svc = MagicMock()
        svc.verify_token.side_effect = ValueError("expired")
        req.app.state.auth_service = svc
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(req)
        assert exc_info.value.status_code == 401


# ===========================================================================
# require_role
# ===========================================================================


class TestRequireRole:
    def test_admin_role_passes(self) -> None:
        user = TokenPayload(sub="u1", role=Role.ADMIN, exp=0, iat=0)
        req = _mock_request(user=user)
        checker = require_role(Role.ADMIN)
        assert checker(req) == user

    def test_viewer_denied_admin(self) -> None:
        user = TokenPayload(sub="u1", role=Role.VIEWER, exp=0, iat=0)
        req = _mock_request(user=user)
        checker = require_role(Role.ADMIN)
        with pytest.raises(HTTPException) as exc_info:
            checker(req)
        assert exc_info.value.status_code == 403

    def test_editor_can_viewer(self) -> None:
        user = TokenPayload(sub="u1", role=Role.EDITOR, exp=0, iat=0)
        req = _mock_request(user=user)
        checker = require_role(Role.VIEWER)
        assert checker(req) == user

    def test_no_user_no_auth_service_denied(self) -> None:
        req = _mock_request()
        # auth_service not set, config.auth.allow_unauthenticated_access = False
        cfg = req.app.state.config
        cfg.auth = MagicMock()
        cfg.auth.allow_unauthenticated_access = False
        checker = require_role(Role.VIEWER)
        with pytest.raises(HTTPException) as exc_info:
            checker(req)
        assert exc_info.value.status_code == 403

    def test_allow_unauthenticated_access(self) -> None:
        req = _mock_request()
        # auth_service not set, but allow_unauthenticated_access = True
        cfg = req.app.state.config
        cfg.auth.allow_unauthenticated_access = True
        checker = require_role(Role.VIEWER)
        result = checker(req)
        assert result.role == Role.VIEWER
        assert result.sub == "anonymous"

    def test_unauthenticated_denied_for_admin(self) -> None:
        req = _mock_request()
        # auth_service not set, allow_unauthenticated_access = True but admin required
        cfg = req.app.state.config
        cfg.auth.allow_unauthenticated_access = True
        checker = require_role(Role.ADMIN)
        with pytest.raises(HTTPException) as exc_info:
            checker(req)
        assert exc_info.value.status_code == 403
