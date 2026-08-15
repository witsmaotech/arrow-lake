"""v1.10.5 M3 — JWKS endpoint + RS256 wiring.

``GET /api/v1/auth/jwks`` serves the public signing key as a JWK Set for
asymmetric algorithms (RS*/PS*/ES*); HS256 deployments get a 404 (a symmetric
secret has no public half to distribute). This is the seam a future external
IdP or API gateway will verify tokens through.
"""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("jwt")
pytest.importorskip("cryptography")

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa


def _rsa_pair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return pub, priv


def _es256_pair() -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return pub, priv


# ---------------------------------------------------------------------------
# AuthService.jwks()
# ---------------------------------------------------------------------------
class TestAuthServiceJwks:
    def test_rs256_jwk_shape_and_kid(self) -> None:
        from arrow_lake.api.auth_service import AuthService

        pub, priv = _rsa_pair()
        svc = AuthService(algorithm="RS256", public_key=pub, private_key=priv)
        jwks = svc.jwks()
        assert jwks is not None
        (jwk,) = jwks["keys"]
        assert jwk["kty"] == "RSA"
        assert jwk["n"] and jwk["e"]
        assert jwk["alg"] == "RS256"
        assert jwk["use"] == "sig"
        assert jwk["kid"] == hashlib.sha256(pub.encode()).hexdigest()[:8]

    def test_es256_jwk_shape(self) -> None:
        from arrow_lake.api.auth_service import AuthService

        pub, priv = _es256_pair()
        svc = AuthService(algorithm="ES256", public_key=pub, private_key=priv)
        jwks = svc.jwks()
        assert jwks is not None
        (jwk,) = jwks["keys"]
        assert jwk["kty"] == "EC" and jwk["x"] and jwk["y"] and jwk["crv"]

    def test_hs256_returns_none(self) -> None:
        from arrow_lake.api.auth_service import AuthService

        svc = AuthService(secret_key="x" * 44)
        assert svc.jwks() is None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_jwks_endpoint_rs256_public_and_verifiable() -> None:
    from unittest.mock import MagicMock

    from httpx import ASGITransport, AsyncClient

    from arrow_lake.api.app import create_app
    from arrow_lake.config import ArrowLakeConfig

    pub, priv = _rsa_pair()
    config = ArrowLakeConfig()
    config.auth.auth_mode = "jwt"
    config.auth.jwt_algorithm = "RS256"
    config.auth.jwt_public_key = pub
    config.auth.jwt_private_key = priv
    config.auth.jwt_secret_key = ""
    config.api.api_key = ""
    config.redis.enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()

    svc = app.state.auth_service
    assert svc is not None  # RS256-only wiring must still build the service
    token = svc._encode(svc.create_access_token(user_id="7"))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        # No Authorization header — the public key is public by definition.
        r = await ac.get("/api/v1/auth/jwks")
        assert r.status_code == 200
        jwk = r.json()["keys"][0]

    # A third-party verifier: JWK → public key → verify the minted token.
    key = jwt.PyJWK.from_dict(jwk).key
    payload = jwt.decode(token, key, algorithms=["RS256"], options={"verify_aud": False})
    assert payload["sub"] == "7"


@pytest.mark.asyncio
async def test_jwks_endpoint_hs256_404() -> None:
    from unittest.mock import MagicMock

    from httpx import ASGITransport, AsyncClient

    from arrow_lake.api.app import create_app
    from arrow_lake.config import ArrowLakeConfig

    config = ArrowLakeConfig()
    config.auth.auth_mode = "jwt"
    config.auth.jwt_secret_key = "x" * 44
    config.api.api_key = ""
    config.redis.enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/v1/auth/jwks")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_rs256_only_still_enforces_auth_middleware() -> None:
    """RS256 without any symmetric secret must not silently skip the JWT
    middleware (wiring bug the JWKS work surfaced)."""
    from unittest.mock import MagicMock

    from httpx import ASGITransport, AsyncClient

    from arrow_lake.api.app import create_app
    from arrow_lake.config import ArrowLakeConfig

    pub, priv = _rsa_pair()
    config = ArrowLakeConfig()
    config.auth.auth_mode = "jwt"
    config.auth.jwt_algorithm = "RS256"
    config.auth.jwt_public_key = pub
    config.auth.jwt_private_key = priv
    config.auth.jwt_secret_key = ""
    config.api.api_key = ""
    config.redis.enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/v1/auth/me")
        assert r.status_code == 401  # middleware active, not skipped
