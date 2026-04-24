"""Tests for API Key authentication middleware."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from arrow_lake.api.app import create_app
from arrow_lake.api.auth import ApiKeyMiddleware
from unittest.mock import MagicMock


def _make_app_with_key(api_key: str) -> "FastAPI":
    from fastapi import FastAPI

    app = create_app()
    app.add_middleware(ApiKeyMiddleware, api_key=api_key)
    app.state.lake = MagicMock()
    return app


@pytest.mark.asyncio
async def test_no_api_key_configured_rejects_protected() -> None:
    """When api_key is empty, protected endpoints are rejected (defense-in-depth)."""
    app = _make_app_with_key("")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/datasets/test/ingest", json={"file_paths": ["a.csv"]})
        assert resp.status_code == 401
        assert "not configured" in resp.json()["message"]


@pytest.mark.asyncio
async def test_valid_api_key_accepted() -> None:
    """A correct API key should not be rejected."""
    app = _make_app_with_key("secret-key")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "secret-key"},
    ) as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_missing_api_key_rejected() -> None:
    """Missing API key on protected endpoint returns 401."""
    app = _make_app_with_key("secret-key")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/datasets/test/ingest", json={"file_paths": ["a.csv"]})
        assert resp.status_code == 401
        assert "API key" in resp.json()["message"]


@pytest.mark.asyncio
async def test_wrong_api_key_rejected() -> None:
    """Wrong API key returns 401."""
    app = _make_app_with_key("secret-key")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "wrong-key"},
    ) as ac:
        resp = await ac.post("/api/v1/datasets/test/ingest", json={"file_paths": ["a.csv"]})
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_public_paths_bypass_auth() -> None:
    """Public paths should be accessible without API key."""
    app = _make_app_with_key("secret-key")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # /health is public
        resp = await ac.get("/health")
        assert resp.status_code == 200

        # /docs is public
        resp = await ac.get("/docs", follow_redirects=False)
        assert resp.status_code in (200, 307)

        # /openapi.json is public
        resp = await ac.get("/openapi.json")
        assert resp.status_code == 200
