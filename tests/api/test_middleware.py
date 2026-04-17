"""Tests for GZip compression and request size limit middleware."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from arrow_lake.api.app import create_app


@pytest.fixture
async def client() -> AsyncClient:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# GZip compression
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gzip_accepted_for_large_response(client: AsyncClient) -> None:
    """Responses include content-encoding when client accepts gzip."""
    resp = await client.get(
        "/openapi.json",
        headers={"accept-encoding": "gzip"},
    )
    assert resp.status_code == 200
    # OpenAPI spec is > 1000 bytes, so it should be compressed
    assert "content-encoding" in resp.headers
    assert resp.headers["content-encoding"] == "gzip"


@pytest.mark.asyncio
async def test_no_gzip_when_not_accepted(client: AsyncClient) -> None:
    """No compression when client doesn't accept gzip."""
    resp = await client.get(
        "/openapi.json",
        headers={"accept-encoding": "identity"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") != "gzip"


# ---------------------------------------------------------------------------
# Request size limit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_oversized_request_rejected(client: AsyncClient) -> None:
    """Requests exceeding max size return 413."""
    # Default max is 100MB, so we'd need to actually send a huge body.
    # Instead, test with a custom app that has a tiny limit.
    from arrow_lake.api.middleware import RequestSizeLimitMiddleware
    from arrow_lake.config import ArrowLakeConfig
    from fastapi import FastAPI

    tiny_app = FastAPI()
    tiny_app.add_middleware(RequestSizeLimitMiddleware, max_size_bytes=10)

    @tiny_app.post("/test")
    async def test_endpoint() -> dict:
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=tiny_app), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/test",
            content="x" * 100,
            headers={"content-length": "100"},
        )
        assert resp.status_code == 413
        body = resp.json()
        assert body["error"] == "REQUEST_TOO_LARGE"


@pytest.mark.asyncio
async def test_normal_request_accepted(client: AsyncClient) -> None:
    """Normal-sized requests pass through."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_sensitive_info_in_health(client: AsyncClient) -> None:
    """Health endpoint doesn't leak configuration details."""
    resp = await client.get("/health")
    body = resp.json()
    # Should not contain sensitive config values
    assert "api_key" not in body
    assert "secret" not in body
    assert "password" not in body


# ---------------------------------------------------------------------------
# Backward compatibility — old server.py still importable
# ---------------------------------------------------------------------------

def test_old_server_importable() -> None:
    """Verify the deprecated arrow_lake.server module still exists."""
    import importlib
    with pytest.warns(DeprecationWarning):
        importlib.reload(importlib.import_module("arrow_lake.server"))
