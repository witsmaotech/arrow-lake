"""v1.10.5 follow-ups — the two items deferred from the M-milestone reviews.

1. H2: ``_extract_client_ip`` honored X-Forwarded-For unconditionally, so a
   direct client could rotate a spoofed XFF to dodge per-IP lockout/rate
   limits. Now XFF is honored ONLY when the direct peer is itself a
   configured trusted proxy (or ``*`` opts in explicitly). Default (empty
   set) = peer IP only.
2. both-mode middleware order: the JWT middleware sits OUTSIDE the api-key
   middleware and rejected X-API-Key-only requests with 401 before the
   api-key layer ever ran — breaking the documented "Bearer OR X-API-Key"
   semantics of auth_mode=both. The JWT middleware now delegates
   header-carrying requests to the inner api-key middleware.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from arrow_lake.api.rate_limit import _extract_client_ip

SECRET = "test-secret-key-min-32-chars-for-hmac!"


def _req(xff: str | None = None, peer: str = "203.0.113.9") -> MagicMock:
    request = MagicMock(spec=Request)
    request.headers = {"x-forwarded-for": xff} if xff else {}
    request.client = MagicMock()
    request.client.host = peer
    return request


# ---------------------------------------------------------------------------
# 1. _extract_client_ip peer-trust gate
# ---------------------------------------------------------------------------
class TestExtractClientIp:
    def test_xff_ignored_when_peer_untrusted_default(self) -> None:
        """Direct client fully controls XFF — with no trusted proxies
        configured, it must be ignored (peer IP wins)."""
        assert _extract_client_ip(_req(xff="1.2.3.4, 5.6.7.8"), set()) == "203.0.113.9"

    def test_xff_honored_when_peer_is_trusted_proxy(self) -> None:
        # peer 10.0.0.2 is our reverse proxy; XFF lists real client + proxy
        req = _req(xff="198.51.100.7, 10.0.0.2", peer="10.0.0.2")
        assert _extract_client_ip(req, {"10.0.0.2"}) == "198.51.100.7"

    def test_wildcard_opt_in_honors_xff_from_any_peer(self) -> None:
        req = _req(xff="198.51.100.7", peer="203.0.113.9")
        assert _extract_client_ip(req, {"*"}) == "198.51.100.7"

    def test_all_xff_trusted_takes_leftmost(self) -> None:
        req = _req(xff="10.0.0.2, 10.0.0.3", peer="10.0.0.2")
        assert _extract_client_ip(req, {"10.0.0.2", "10.0.0.3"}) == "10.0.0.2"

    def test_no_xff_returns_peer(self) -> None:
        assert _extract_client_ip(_req(), {"10.0.0.2"}) == "203.0.113.9"

    def test_no_client_returns_unknown(self) -> None:
        request = MagicMock(spec=Request)
        request.headers = {}
        request.client = None
        assert _extract_client_ip(request, set()) == "unknown"


@pytest.mark.asyncio
async def test_rotating_xff_cannot_dodge_lockout() -> None:
    """Integration: the password-reset lockout keys on the peer IP, so a
    rotating spoofed XFF no longer yields a fresh bucket per attempt."""
    from httpx import ASGITransport, AsyncClient

    import arrow_lake.api.routers.auth as auth_router
    from arrow_lake.api.app import create_app
    from arrow_lake.config import ArrowLakeConfig

    config = ArrowLakeConfig()
    config.auth.auth_mode = "jwt"
    config.auth.jwt_secret_key = SECRET
    config.api.api_key = ""
    config.redis.enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()
    from arrow_lake.system_db import Migrator, SystemDB
    from arrow_lake.system_db.stores import IdentityStore

    db = SystemDB(":memory:")
    Migrator(db).run()
    app.state.identity_store = IdentityStore(db)

    orig_limit = auth_router._LOGIN_FAIL_LIMIT
    auth_router._LOGIN_FAILURES.clear()
    auth_router._LOGIN_FAIL_LIMIT = 3
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            for i in range(3):
                r = await ac.post(
                    "/api/v1/auth/password-reset",
                    headers={"X-Forwarded-For": f"1.2.3.{i}"},  # rotate spoofed XFF
                    json={"token": "alr_garbage", "new_password": "long-enough-1"},
                )
                assert r.status_code == 401
            locked = await ac.post(
                "/api/v1/auth/password-reset",
                headers={"X-Forwarded-For": "9.9.9.9"},
                json={"token": "alr_garbage", "new_password": "long-enough-1"},
            )
            assert locked.status_code == 429  # same peer → same bucket
    finally:
        auth_router._LOGIN_FAIL_LIMIT = orig_limit
        auth_router._LOGIN_FAILURES.clear()


# ---------------------------------------------------------------------------
# 2. both-mode delegation in the JWT middleware
# ---------------------------------------------------------------------------
def _mw_request(headers: dict, path: str = "/api/v1/datasets") -> MagicMock:
    request = MagicMock(spec=Request)
    request.url = MagicMock()
    request.url.path = path
    request.method = "GET"
    request.headers = headers
    request.state = MagicMock()
    return request


class TestJwtMiddlewareDelegation:
    @pytest.mark.asyncio
    async def test_api_key_header_delegates(self) -> None:
        from arrow_lake.api.auth_service import AuthService
        from arrow_lake.api.jwt_auth import jwt_auth_middleware_fn

        svc = AuthService(secret_key=SECRET)
        request = _mw_request({"X-API-Key": "alr_or_shared"})
        call_next = AsyncMock(return_value=JSONResponse(status_code=200, content={"ok": 1}))
        response = await jwt_auth_middleware_fn(
            request, call_next, auth_service=svc, api_key_header="X-API-Key"
        )
        assert response.status_code == 200
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_no_header_no_bearer_still_401(self) -> None:
        from arrow_lake.api.auth_service import AuthService
        from arrow_lake.api.jwt_auth import jwt_auth_middleware_fn

        svc = AuthService(secret_key=SECRET)
        request = _mw_request({})
        call_next = AsyncMock(return_value=JSONResponse(status_code=200, content={"ok": 1}))
        response = await jwt_auth_middleware_fn(
            request, call_next, auth_service=svc, api_key_header="X-API-Key"
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_no_delegation_configured_still_401(self) -> None:
        """api_key_header=None (jwt mode / api-key infra absent) → no bypass."""
        from arrow_lake.api.auth_service import AuthService
        from arrow_lake.api.jwt_auth import jwt_auth_middleware_fn

        svc = AuthService(secret_key=SECRET)
        request = _mw_request({"X-API-Key": "whatever"})
        call_next = AsyncMock(return_value=JSONResponse(status_code=200, content={"ok": 1}))
        response = await jwt_auth_middleware_fn(request, call_next, auth_service=svc)
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_both_mode_api_key_only_request_passes() -> None:
    """End-to-end: auth_mode=both honours 'Bearer OR X-API-Key'."""
    from httpx import ASGITransport, AsyncClient

    from arrow_lake.api.app import create_app
    from arrow_lake.config import ArrowLakeConfig

    config = ArrowLakeConfig()
    config.auth.auth_mode = "both"
    config.auth.jwt_secret_key = SECRET
    config.api.api_key = "shared-key"
    config.api.docs_enabled = False
    config.redis.enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/v1/datasets", headers={"X-API-Key": "shared-key"})
        assert r.status_code != 401  # passed the auth layers (mock lake may 200/500)
