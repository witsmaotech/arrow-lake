"""Targeted tests for api/auth_service.py — uncovered paths."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("jwt")

from arrow_lake.api.auth_models import Role
from arrow_lake.api.auth_service import AuthService

SECRET = "test-secret-key-min-32-chars-for-hmac!"


def _svc(**kw) -> AuthService:
    defaults = dict(secret_key=SECRET, algorithm="HS256", access_token_minutes=30, refresh_token_days=7)
    defaults.update(kw)
    return AuthService(**defaults)


class TestVerifyTokenRefresh:
    def test_refresh_token_accepted(self) -> None:
        svc = _svc()
        token = svc.create_refresh_token(user_id="u1", role=Role.VIEWER)
        result = svc.verify_token(token, require_refresh=True)
        assert result.sub == "u1"
