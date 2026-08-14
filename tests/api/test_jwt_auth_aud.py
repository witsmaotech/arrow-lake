"""v1.10.5 M0 — JWT `aud` claim hardening.

Covers: tokens carry `aud`; verification enforces it only when
``jwt_require_audience`` is enabled (legacy tokens without ``aud`` stay valid
while the flag is False — compatibility window).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytest.importorskip("jwt")

from unittest.mock import MagicMock

from arrow_lake.api.app import create_app
from arrow_lake.api.auth_service import AuthService
from arrow_lake.config import ArrowLakeConfig

SECRET = "test-secret-key-min-32-chars-for-hmac!"


# ---------------------------------------------------------------------------
# AuthService unit level
# ---------------------------------------------------------------------------
class TestAudClaim:
    def test_created_token_carries_aud(self) -> None:
        svc = AuthService(secret_key=SECRET, audience="arrow-lake-api")
        token = svc.create_refresh_token(user_id="42", role="viewer")
        payload = svc.verify_token(token)
        assert payload.aud == "arrow-lake-api"

    def test_default_audience_value(self) -> None:
        svc = AuthService(secret_key=SECRET)
        assert svc._audience == ""
        token = svc.create_refresh_token(user_id="42")
        # No audience configured → aud stays empty (back-compat).
        assert svc.verify_token(token).aud == ""

    def test_wrong_aud_rejected_when_required(self) -> None:
        svc = AuthService(secret_key=SECRET, audience="expected-aud", require_audience=True)
        other = AuthService(secret_key=SECRET, audience="other-aud")
        token = other.create_refresh_token(user_id="42")
        with pytest.raises(ValueError, match="audience"):
            svc.verify_token(token)

    def test_correct_aud_accepted_when_required(self) -> None:
        svc = AuthService(secret_key=SECRET, audience="expected-aud", require_audience=True)
        token = svc.create_refresh_token(user_id="42")
        assert svc.verify_token(token).sub == "42"

    def test_missing_aud_rejected_when_required(self) -> None:
        """Legacy token (no aud) fails once require_audience=True."""
        svc = AuthService(secret_key=SECRET, audience="arrow-lake-api", require_audience=True)
        legacy = AuthService(secret_key=SECRET)  # no audience → no aud claim value
        token = legacy.create_refresh_token(user_id="42")
        with pytest.raises(ValueError, match="audience"):
            svc.verify_token(token)

    def test_missing_aud_allowed_when_not_required(self) -> None:
        """Compatibility window: legacy token still verifies with flag off."""
        svc = AuthService(secret_key=SECRET, audience="arrow-lake-api", require_audience=False)
        legacy = AuthService(secret_key=SECRET)
        token = legacy.create_refresh_token(user_id="42")
        assert svc.verify_token(token).sub == "42"


# ---------------------------------------------------------------------------
# Endpoint level (middleware path)
# ---------------------------------------------------------------------------
def _make_app(require_aud: bool) -> FastAPI:
    config = ArrowLakeConfig()
    config.auth.auth_mode = "jwt"
    config.auth.jwt_secret_key = SECRET
    config.auth.jwt_bootstrap_token = "bootstrap"
    config.auth.jwt_audience = "arrow-lake-api"
    config.auth.jwt_require_audience = require_aud
    config.api.api_key = ""
    app = create_app(config=config)
    app.state.lake = MagicMock()
    return app


@pytest.mark.asyncio
async def test_middleware_rejects_wrong_aud_when_required() -> None:
    app = _make_app(require_aud=True)
    # Token minted by a service with a different audience.
    foreign = AuthService(secret_key=SECRET, audience="some-other-api")
    token = foreign.create_refresh_token(user_id="42")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["error"] == "AUTH_INVALID_TOKEN"


@pytest.mark.asyncio
async def test_middleware_accepts_legacy_token_when_not_required() -> None:
    app = _make_app(require_aud=False)
    legacy = AuthService(secret_key=SECRET)  # pre-v1.10.5 token without aud
    token = legacy.create_refresh_token(user_id="42")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_login_token_carries_configured_aud() -> None:
    app = _make_app(require_aud=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            "/api/v1/auth/token", headers={"Authorization": "Bearer bootstrap"}
        )
    assert resp.status_code == 200
    svc: AuthService = app.state.auth_service
    pair = resp.json()
    payload = svc.verify_token(pair["access_token"])
    assert payload.aud == "arrow-lake-api"


def test_token_payload_model_has_aud_field() -> None:
    from arrow_lake.api.auth_models import TokenPayload

    now = datetime.now(UTC)
    p = TokenPayload(sub="1", role="viewer", exp=now, iat=now, aud="arrow-lake-api")
    assert p.aud == "arrow-lake-api"
