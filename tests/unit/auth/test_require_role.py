"""Tests for require_role() dependency — H9 security fix."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from arrow_lake.api.deps import require_role
from arrow_lake.api.auth_models import Role, TokenPayload


def _mock_request(*, has_auth_service: bool = False, allow_unauth: bool = False):
    """Build a mock Request with configurable app.state."""
    request = MagicMock()
    request.state.user = None
    request.app.state.auth_service = MagicMock() if has_auth_service else None

    if not has_auth_service:
        cfg = MagicMock()
        cfg.auth.allow_unauthenticated_access = allow_unauth
        request.app.state.config = cfg
    else:
        request.app.state.config = MagicMock()

    return request


class TestRequireRoleNoAuthService:
    """When no auth_service is configured."""

    def test_default_deny(self):
        """Without allow_unauthenticated_access, should raise 403."""
        check = require_role(Role.ADMIN)
        request = _mock_request(has_auth_service=False, allow_unauth=False)

        with pytest.raises(HTTPException) as exc_info:
            check(request)
        assert exc_info.value.status_code == 403
        assert "not configured" in exc_info.value.detail

    def test_allow_when_configured(self):
        """With allow_unauthenticated_access=True, returns anonymous ADMIN."""
        check = require_role(Role.ADMIN)
        request = _mock_request(has_auth_service=False, allow_unauth=True)

        result = check(request)
        assert result.role == Role.ADMIN
        assert result.sub == "anonymous"

    def test_deny_lower_role_still_denied(self):
        """Even with allow_unauth, if auth_service is None, behavior is same."""
        check = require_role(Role.EDITOR)
        request = _mock_request(has_auth_service=False, allow_unauth=True)

        result = check(request)
        assert result.role == Role.ADMIN  # anonymous gets ADMIN


class TestRequireRoleWithAuthService:
    """When auth_service is configured."""

    def test_sufficient_role(self):
        """User with ADMIN role passes ADMIN requirement."""
        check = require_role(Role.ADMIN)
        request = _mock_request(has_auth_service=True)
        user = TokenPayload(sub="user1", role=Role.ADMIN, exp=0, iat=0)
        with patch("arrow_lake.api.deps.get_current_user", return_value=user):
            result = check(request)
        assert result.sub == "user1"
        assert result.role == Role.ADMIN

    def test_insufficient_role(self):
        """VIEWER user fails EDITOR requirement."""
        check = require_role(Role.EDITOR)
        request = _mock_request(has_auth_service=True)
        user = TokenPayload(sub="user1", role=Role.VIEWER, exp=0, iat=0)
        with patch("arrow_lake.api.deps.get_current_user", return_value=user):
            with pytest.raises(HTTPException) as exc_info:
                check(request)
        assert exc_info.value.status_code == 403

    def test_viewer_passes_viewer_requirement(self):
        """VIEWER user passes VIEWER requirement."""
        check = require_role(Role.VIEWER)
        request = _mock_request(has_auth_service=True)
        user = TokenPayload(sub="user1", role=Role.VIEWER, exp=0, iat=0)
        with patch("arrow_lake.api.deps.get_current_user", return_value=user):
            result = check(request)
        assert result.role == Role.VIEWER
