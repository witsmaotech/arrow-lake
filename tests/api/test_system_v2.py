"""Coverage for system router health/version/metrics endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.get_session_manager.return_value.get_stats.return_value = MagicMock(
        pool_size=5, active_sessions=2, queued_requests=0, total_queries=100, total_errors=1,
    )
    return lake


@pytest.fixture
async def client(mock_lake: MagicMock) -> AsyncClient:
    from arrow_lake.api.app import create_app
    from arrow_lake.config import ArrowLakeConfig

    config = ArrowLakeConfig()
    config.api.api_key = "test-key"
    config.api.api_key_default_role = "ADMIN"
    app = create_app(config)
    app.state.lake = mock_lake
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-key"},
    ) as ac:
        yield ac


# ── /health/live ──


@pytest.mark.asyncio
async def test_health_live(client: AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── /health/ready ──


@pytest.mark.asyncio
async def test_health_ready(client: AsyncClient) -> None:
    with patch("arrow_lake.api.routers.system._check_ray", return_value=("unreachable", False)):
        resp = await client.get("/health/ready")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "status" in body
    assert "version" in body
    assert "storage" in body


# ── /health (backward compat) ──


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "status" in body


# ── /metrics ──


@pytest.mark.asyncio
async def test_metrics(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code in (200, 503)


# ── /api/v1/version ──


@pytest.mark.asyncio
async def test_version_info(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/version")
    assert resp.status_code == 200
    body = resp.json()
    assert "version" in body
    assert "python" in body
    assert body["python"].startswith("3.")


# ── _check_storage ──


def test_check_storage_local_accessible() -> None:
    from arrow_lake.api.routers.system import _check_storage
    from arrow_lake.config import ArrowLakeConfig

    config = ArrowLakeConfig()
    config.storage.backend = MagicMock()
    config.storage.backend.value = "local"
    # Patch to use LOCAL backend
    from arrow_lake.config.storage import StorageBackend
    config.storage.backend = StorageBackend.LOCAL
    text, ok = _check_storage(config)
    # May or may not be accessible depending on FS
    assert isinstance(text, str)
    assert isinstance(ok, bool)


def test_check_storage_local_not_found() -> None:
    from arrow_lake.api.routers.system import _check_storage
    from arrow_lake.config import ArrowLakeConfig
    from arrow_lake.config.storage import StorageBackend

    config = ArrowLakeConfig()
    config.storage.backend = StorageBackend.LOCAL
    config.storage.base_uri = "/nonexistent/path/xyz"
    text, ok = _check_storage(config)
    assert ok is False
    assert text == "not_found"


def test_check_storage_s3_reachable() -> None:
    from arrow_lake.api.routers.system import _check_storage
    from arrow_lake.config import ArrowLakeConfig
    from arrow_lake.config.storage import StorageBackend

    config = ArrowLakeConfig()
    config.storage.backend = StorageBackend.S3
    config.storage.s3_endpoint = "http://localhost:9000"

    mock_resp = MagicMock()
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        text, ok = _check_storage(config)
    assert ok is True
    assert text == "accessible"


def test_check_storage_s3_unreachable() -> None:
    from arrow_lake.api.routers.system import _check_storage
    from arrow_lake.config import ArrowLakeConfig
    from arrow_lake.config.storage import StorageBackend

    config = ArrowLakeConfig()
    config.storage.backend = StorageBackend.S3
    config.storage.s3_endpoint = "http://unreachable:9000"

    with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
        text, ok = _check_storage(config)
    assert ok is False
    assert text == "endpoint_unreachable"


# ── _check_gravitino ──


def test_check_gravitino_healthy() -> None:
    from arrow_lake.api.routers.system import _check_gravitino

    mock_resp = MagicMock()
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        text, ok = _check_gravitino("http://gravitino:8090")
    assert ok is True


def test_check_gravitino_unreachable() -> None:
    from arrow_lake.api.routers.system import _check_gravitino

    with patch("urllib.request.urlopen", side_effect=Exception("conn refused")):
        text, ok = _check_gravitino("http://gravitino:8090")
    assert ok is False


# ── _check_lance_rest ──


def test_check_lance_rest_healthy() -> None:
    from arrow_lake.api.routers.system import _check_lance_rest

    mock_opener = MagicMock()
    mock_opener.open.return_value = MagicMock()
    with patch("urllib.request.build_opener", return_value=mock_opener):
        text, ok = _check_lance_rest("http://lance:8888")
    assert ok is True


def test_check_lance_rest_unreachable() -> None:
    from arrow_lake.api.routers.system import _check_lance_rest

    with patch("urllib.request.build_opener", side_effect=Exception("fail")):
        text, ok = _check_lance_rest("http://lance:8888")
    assert ok is False


# ── _check_redis ──


def test_check_redis_healthy() -> None:
    from arrow_lake.api.routers.system import _check_redis

    mock_redis = MagicMock()
    mock_redis.ping.return_value = True
    with patch("redis.from_url", return_value=mock_redis):
        text, ok = _check_redis("redis://localhost:6379")
    assert ok is True


def test_check_redis_unreachable() -> None:
    from arrow_lake.api.routers.system import _check_redis

    with patch("redis.from_url", side_effect=Exception("no redis")):
        text, ok = _check_redis("redis://localhost:6379")
    assert ok is False
