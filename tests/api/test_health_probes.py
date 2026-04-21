"""Tests for liveness and readiness probe endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock

from fastapi import FastAPI

from arrow_lake.api.app import create_app


def _make_app(storage_dir: str | None = None) -> FastAPI:
    """Create a test app with optional storage override."""
    from arrow_lake.config import ArrowLakeConfig, StorageConfig

    if storage_dir is not None:
        config = ArrowLakeConfig()
        config.storage = StorageConfig(base_uri=storage_dir)
        app = create_app(config=config)
    else:
        app = create_app()
    app.state.lake = MagicMock()
    return app


# ---------------------------------------------------------------------------
# /health/live — liveness probe (always 200 if process is running)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_liveness_always_200() -> None:
    """GET /health/live should always return 200 when the process is running."""
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health/live")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_liveness_with_valid_storage() -> None:
    """GET /health/live returns 200 regardless of storage state."""
    app = _make_app(storage_dir="/tmp/nonexistent_dir")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health/live")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /health/ready — readiness probe (checks storage dependencies)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readiness_ok_with_valid_storage() -> None:
    """GET /health/ready returns 200 when storage is accessible."""
    app = _make_app(storage_dir="/tmp")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["storage"] == "accessible"


@pytest.mark.asyncio
async def test_readiness_degraded_with_invalid_storage() -> None:
    """GET /health/ready returns 503 when storage is not accessible."""
    app = _make_app(storage_dir="/tmp/nonexistent_arrow_lake_test_dir")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["storage"] == "not_found"


# ---------------------------------------------------------------------------
# /health — backward compatibility (same behavior as before)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_backward_compat() -> None:
    """GET /health still works as before (backward compatibility)."""
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("ok", "degraded")
        assert "version" in body
        assert "storage" in body


@pytest.mark.asyncio
async def test_health_backward_compat_degraded() -> None:
    """GET /health returns 503 when storage is not accessible (backward compat)."""
    app = _make_app(storage_dir="/tmp/nonexistent_arrow_lake_test_dir")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"


# ---------------------------------------------------------------------------
# Public paths — health probes bypass auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_probes_bypass_auth() -> None:
    """Health probe endpoints should be accessible without API key."""
    from arrow_lake.api.auth import ApiKeyMiddleware

    app = _make_app()
    app.add_middleware(ApiKeyMiddleware, api_key="test-secret-key")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health/live")
        assert resp.status_code == 200

        resp = await ac.get("/health/ready")
        assert resp.status_code in (200, 503)  # depends on storage
