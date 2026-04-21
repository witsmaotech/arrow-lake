"""Unit tests for AuthService (JWT token creation/verification)."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.api.auth_service import AuthService


@pytest.fixture
def auth_service() -> AuthService:
    return AuthService(secret_key="test-secret-key-for-unit-tests")


# ---------------------------------------------------------------------------
# create_access_token / verify_token
# ---------------------------------------------------------------------------


def test_create_and_verify_access_token(auth_service: AuthService) -> None:
    payload = auth_service.create_access_token(
        user_id="user-123", role=Role.ADMIN, permissions=["read", "write"]
    )
    assert payload.sub == "user-123"
    assert payload.role == Role.ADMIN
    assert payload.permissions == ["read", "write"]
    assert payload.iss == "arrow-lake"

    token = auth_service._encode(payload)
    verified = auth_service.verify_token(token)
    assert verified.sub == "user-123"
    assert verified.role == Role.ADMIN


def test_create_access_token_default_role(auth_service: AuthService) -> None:
    payload = auth_service.create_access_token(user_id="user-1")
    assert payload.role == Role.VIEWER
    assert payload.permissions == []


# ---------------------------------------------------------------------------
# refresh token
# ---------------------------------------------------------------------------


def test_create_and_use_refresh_token(auth_service: AuthService) -> None:
    refresh_token = auth_service.create_refresh_token(user_id="user-1")
    assert isinstance(refresh_token, str)
    assert len(refresh_token) > 0

    new_payload = auth_service.refresh_access_token(refresh_token)
    assert new_payload.sub == "user-1"
    assert new_payload.role == Role.VIEWER


def test_refresh_token_preserves_role(auth_service: AuthService) -> None:
    refresh_token = auth_service.create_refresh_token(
        user_id="admin-1", role=Role.ADMIN, permissions=["all"]
    )
    new_payload = auth_service.refresh_access_token(refresh_token)
    assert new_payload.role == Role.ADMIN
    assert new_payload.permissions == ["all"]


# ---------------------------------------------------------------------------
# verify_token errors
# ---------------------------------------------------------------------------


def test_verify_invalid_token(auth_service: AuthService) -> None:
    with pytest.raises(ValueError, match="Invalid token"):
        auth_service.verify_token("not.a.valid.jwt")


def test_verify_wrong_secret(auth_service: AuthService) -> None:
    other_service = AuthService(secret_key="wrong-secret")
    payload = auth_service.create_access_token(user_id="user-1")
    token = auth_service._encode(payload)
    with pytest.raises(ValueError, match="Invalid token"):
        other_service.verify_token(token)


def test_verify_expired_token(auth_service: AuthService) -> None:
    from unittest.mock import patch

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = TokenPayload(
        sub="user-1",
        role=Role.VIEWER,
        exp=past,
        iat=past - timedelta(minutes=30),
    )
    token = auth_service._encode(payload)
    with pytest.raises(ValueError, match="Token expired"):
        auth_service.verify_token(token)
