"""v1.11.0.3 hardening — REST-level pins.

P2-1: ``DELETE /datasets/{name}?table=`` addresses one container table
      (facade threads ``table`` through; registry row reclaimed there).
W2:   ``GET /favicon.ico`` is public — browsers auto-request it on every
      console page load; a 401 there surfaced as the "intermittent
      homepage 401" console error.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient

SECRET = "test-secret-key-min-32-chars-for-hmac!"


async def _client(*, with_key: bool = True) -> AsyncClient:
    config = ArrowLakeConfig()
    config.auth.jwt_secret_key = SECRET
    config.api.api_key = "test-api-key"
    config.api.api_key_default_role = "ADMIN"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()
    # health/metrics deps that lifespan would normally provide
    app.state.checker = MagicMock()
    from arrow_lake.api.auth_service import AuthService

    app.state.auth_service = AuthService(secret_key=SECRET)
    headers = {"X-API-Key": "test-api-key"} if with_key else {}
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=headers,
    ) as ac:
        yield ac


@pytest.fixture
async def client():
    async for ac in _client():
        yield ac


class TestDeleteDatasetTableParam:
    @pytest.mark.asyncio
    async def test_delete_with_table_threads_through(self, client: AsyncClient) -> None:
        r = await client.delete("/api/v1/datasets/gas_net?table=stations")
        assert r.status_code == 200
        assert "stations" in r.json()["message"]

    @pytest.mark.asyncio
    async def test_delete_without_table_unchanged(self, client: AsyncClient) -> None:
        r = await client.delete("/api/v1/datasets/plain_ds")
        assert r.status_code == 200


class TestFaviconPublic:
    @pytest.mark.asyncio
    async def test_favicon_no_credentials_not_401(self) -> None:
        async for ac in _client(with_key=False):
            r = await ac.get("/favicon.ico")
        # public: no 401 — 200 with the asset or 204 fallback, never auth-walled
        assert r.status_code in (200, 204)
        if r.status_code == 200:
            assert r.headers.get("content-type", "").startswith("image/")
