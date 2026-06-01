"""Unit tests for AuthService (JWT token creation/verification)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from arrow_lake.api.auth_models import Role, TokenPayload
from arrow_lake.api.auth_service import AuthService

jwt = pytest.importorskip("jwt")  # skip entire module if PyJWT not installed


@pytest.fixture
def auth_service() -> AuthService:
    return AuthService(secret_key="test-secret-key-for-unit-tests!!")


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
    other_service = AuthService(secret_key="wrong-secret-key-32bytes-minimum!")
    payload = auth_service.create_access_token(user_id="user-1")
    token = auth_service._encode(payload)
    with pytest.raises(ValueError, match="Invalid token"):
        other_service.verify_token(token)


def test_verify_expired_token(auth_service: AuthService) -> None:

    past = datetime.now(UTC) - timedelta(hours=1)
    payload = TokenPayload(
        sub="user-1",
        role=Role.VIEWER,
        exp=past,
        iat=past - timedelta(minutes=30),
    )
    token = auth_service._encode(payload)
    with pytest.raises(ValueError, match="Token expired"):
        auth_service.verify_token(token)


# ---------------------------------------------------------------------------
# RS256 asymmetric algorithm
# ---------------------------------------------------------------------------

cryptography = pytest.importorskip("cryptography", reason="cryptography not installed")


@pytest.fixture
def rsa_keypair():
    """Generate an RSA key pair for RS256 tests."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


@pytest.fixture
def rs256_service(rsa_keypair):
    priv_pem, pub_pem = rsa_keypair
    return AuthService(
        algorithm="RS256",
        public_key=pub_pem,
        private_key=priv_pem,
    )


def test_rs256_create_and_verify(rs256_service: AuthService) -> None:
    payload = rs256_service.create_access_token(
        user_id="user-rsa", role=Role.EDITOR, permissions=["read"]
    )
    token = rs256_service._encode(payload)
    verified = rs256_service.verify_token(token)
    assert verified.sub == "user-rsa"
    assert verified.role == Role.EDITOR
    assert verified.permissions == ["read"]


def test_rs256_refresh_token(rs256_service: AuthService) -> None:
    refresh = rs256_service.create_refresh_token(user_id="user-rsa", role=Role.ADMIN)
    new_payload = rs256_service.refresh_access_token(refresh)
    assert new_payload.sub == "user-rsa"
    assert new_payload.role == Role.ADMIN


def test_rs256_wrong_public_key(rsa_keypair) -> None:
    """Token signed with one key can't be verified with a different key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv_pem, pub_pem = rsa_keypair

    svc_sign = AuthService(algorithm="RS256", public_key=pub_pem, private_key=priv_pem)

    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_pub = wrong_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    svc_verify = AuthService(algorithm="RS256", public_key=wrong_pub, private_key=priv_pem)

    payload = svc_sign.create_access_token(user_id="user-1")
    token = svc_sign._encode(payload)
    with pytest.raises(ValueError, match="Invalid token"):
        svc_verify.verify_token(token)


def test_rs256_missing_keys_raises() -> None:
    with pytest.raises(ValueError, match="requires both jwt_public_key and jwt_private_key"):
        AuthService(algorithm="RS256")


def test_hs256_empty_secret_raises() -> None:
    with pytest.raises(ValueError, match="secret_key is required"):
        AuthService(secret_key="", algorithm="HS256")


def test_es256_supported(rsa_keypair) -> None:
    """ES256 also uses asymmetric keys with same validation."""
    with pytest.raises(ValueError, match="requires both"):
        AuthService(algorithm="ES256")
