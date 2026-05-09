"""Unit tests for JWT auth models (Role, TokenPayload, TokenPair)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from arrow_lake.api.auth_models import Role, TokenPair, TokenPayload

# ---------------------------------------------------------------------------
# Role StrEnum
# ---------------------------------------------------------------------------


def test_role_values() -> None:
    assert Role.ADMIN == "admin"
    assert Role.EDITOR == "editor"
    assert Role.VIEWER == "viewer"
    assert len(Role) == 3


def test_role_from_string() -> None:
    assert Role("admin") is Role.ADMIN
    assert Role("editor") is Role.EDITOR
    with pytest.raises(ValueError):
        Role("nonexistent")


# ---------------------------------------------------------------------------
# TokenPayload
# ---------------------------------------------------------------------------


def test_token_payload_creation() -> None:
    now = datetime.now(UTC)
    payload = TokenPayload(
        sub="user-123",
        role=Role.ADMIN,
        permissions=["read", "write"],
        exp=now + timedelta(minutes=30),
        iat=now,
        iss="arrow-lake",
    )
    assert payload.sub == "user-123"
    assert payload.role == Role.ADMIN
    assert payload.permissions == ["read", "write"]
    assert payload.iss == "arrow-lake"


def test_token_payload_default_issuer() -> None:
    now = datetime.now(UTC)
    payload = TokenPayload(
        sub="user-1",
        role=Role.VIEWER,
        exp=now + timedelta(minutes=30),
        iat=now,
    )
    assert payload.iss == "arrow-lake"


def test_token_payload_roundtrip_dict() -> None:
    """TokenPayload can round-trip through dict (used for JWT encode/decode)."""
    now = datetime.now(UTC)
    payload = TokenPayload(
        sub="user-1",
        role=Role.VIEWER,
        permissions=["read"],
        exp=now + timedelta(minutes=30),
        iat=now,
    )
    data = payload.model_dump()
    assert data["sub"] == "user-1"
    assert data["role"] == "viewer"
    restored = TokenPayload.model_validate(data)
    assert restored.sub == payload.sub


# ---------------------------------------------------------------------------
# TokenPair
# ---------------------------------------------------------------------------


def test_token_pair_creation() -> None:
    pair = TokenPair(
        access_token="eyJhbGciOiJIUzI1NiJ9...",
        refresh_token="eyJhbGciOiJIUzI1NiJ9...",
    )
    assert pair.access_token.startswith("eyJ")
    assert pair.refresh_token.startswith("eyJ")
    assert pair.token_type == "bearer"


def test_token_pair_custom_type() -> None:
    pair = TokenPair(
        access_token="abc",
        refresh_token="def",
        token_type="macaroon",
    )
    assert pair.token_type == "macaroon"
