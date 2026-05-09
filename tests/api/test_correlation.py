"""Tests for CorrelationIdMiddleware — request ID propagation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from arrow_lake.api.app import create_app
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _make_app() -> FastAPI:
    app = create_app()
    app.state.lake = MagicMock()
    return app


@pytest.mark.asyncio
async def test_correlation_id_propagated_from_header() -> None:
    """X-Request-ID from request should be returned in response."""
    app = _make_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Request-ID": "test-req-123"},
    ) as ac:
        resp = await ac.get("/health/live")
        assert resp.status_code == 200
        assert resp.headers["X-Request-ID"] == "test-req-123"


@pytest.mark.asyncio
async def test_correlation_id_auto_generated() -> None:
    """When X-Request-ID is not provided, a UUID should be generated."""
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health/live")
        assert resp.status_code == 200
        request_id = resp.headers.get("X-Request-ID")
        assert request_id is not None
        assert len(request_id) == 36  # UUID format: 8-4-4-4-12


@pytest.mark.asyncio
async def test_correlation_id_unique_per_request() -> None:
    """Each request without X-Request-ID should get a unique ID."""
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp1 = await ac.get("/health/live")
        resp2 = await ac.get("/health/live")
        assert resp1.headers["X-Request-ID"] != resp2.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_correlation_id_on_ready_endpoint() -> None:
    """Correlation ID should work on all endpoints, not just /health/live."""
    app = _make_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Request-ID": "ready-test-789"},
    ) as ac:
        resp = await ac.get("/health/ready")
        assert resp.headers["X-Request-ID"] == "ready-test-789"
