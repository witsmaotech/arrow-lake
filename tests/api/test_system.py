"""Tests for system endpoints (/health, /metrics, /api/v1/version)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _make_config() -> ArrowLakeConfig:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    return config


@pytest.mark.asyncio
async def test_health_returns_ok() -> None:
    config = _make_config()
    app: FastAPI = create_app(config=config)
    app.state.lake = MagicMock()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("ok", "degraded")
        assert "version" in body


@pytest.mark.asyncio
async def test_health_includes_version() -> None:
    config = _make_config()
    app: FastAPI = create_app(config=config)
    app.state.lake = MagicMock()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health")
        body = resp.json()
        assert body["version"] != ""


@pytest.mark.asyncio
async def test_version_returns_info() -> None:
    """GET /api/v1/version returns version, python, and dependency info."""
    config = _make_config()
    app: FastAPI = create_app(config=config)
    app.state.lake = MagicMock()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        resp = await ac.get("/api/v1/version")
        assert resp.status_code == 200
        body = resp.json()
        assert "version" in body
        assert body["version"] != ""
        assert "python" in body
        assert "." in body["python"]
        assert "fastapi" in body
