"""Comprehensive tests for system.py — internal helpers, probes, and endpoints."""

from __future__ import annotations

import sys
from io import BytesIO
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from arrow_lake.api.app import create_app
from arrow_lake.api.routers.system import (
    _attach_pool_stats,
    _check_gravitino,
    _check_lance_rest,
    _check_ray,
    _check_redis,
    _check_storage,
    _get_version,
)
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.config.storage import StorageBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: object) -> ArrowLakeConfig:
    """Build a test config; overrides are applied after construction."""
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    config.compute.ray_address = ""  # avoid Ray connection in tests
    for key, val in overrides.items():
        parts = key.split("__")
        obj: object = config
        for part in parts[:-1]:
            obj = getattr(obj, part)
        setattr(obj, parts[-1], val)  # type: ignore[attr-defined]
    return config


def _make_app(config: ArrowLakeConfig | None = None) -> object:
    """Create app and attach a MagicMock lake to app.state."""
    cfg = config or _make_config()
    app = create_app(config=cfg)
    app.state.lake = MagicMock()
    # Simulate lifespan completion: a real worker flips this True once required
    # startup setup finishes. Tests exercise the ready path.
    app.state.ready = True
    return app


# ===================================================================
# 1. _get_version()
# ===================================================================


class TestGetVersion:
    """Tests for _get_version helper."""

    def test_returns_version_when_module_present(self) -> None:
        """When arrow_lake._version is importable, return its __version__."""
        result = _get_version()
        # _version.py exists in this repo with __version__ = "1.5.2"
        assert isinstance(result, str)
        assert result != ""

    def test_returns_empty_on_import_error(self) -> None:
        """When arrow_lake._version cannot be imported, return empty string."""
        cached = sys.modules.pop("arrow_lake._version", None)
        # Insert a module that raises ImportError on attribute access
        bad_mod = ModuleType("arrow_lake._version")
        del bad_mod.__spec__  # type: ignore[attr-defined]
        # Block re-import by keeping sys.modules entry but make import fail
        sys.modules["arrow_lake._version"] = None  # type: ignore[assignment]
        try:
            result = _get_version()
            assert result == ""
        finally:
            if cached is not None:
                sys.modules["arrow_lake._version"] = cached
            else:
                sys.modules.pop("arrow_lake._version", None)


# ===================================================================
# 2. _check_storage()
# ===================================================================


class TestCheckStorage:
    """Tests for _check_storage helper."""

    def test_local_backend_existing_dir(self, tmp_path: object) -> None:
        """LOCAL backend with an existing directory returns accessible."""
        config = _make_config(
            storage__backend=StorageBackend.LOCAL,
            storage__base_uri=str(tmp_path),
        )
        text, ok = _check_storage(config)
        assert text == "accessible"
        assert ok is True

    def test_local_backend_missing_dir(self) -> None:
        """LOCAL backend with a non-existent path returns not_found."""
        config = _make_config(
            storage__backend=StorageBackend.LOCAL,
            storage__base_uri="/tmp/__arrow_lake_nonexistent_dir__",
        )
        text, ok = _check_storage(config)
        assert text == "not_found"
        assert ok is False

    def test_s3_backend_reachable(self) -> None:
        """MINIO backend with a reachable endpoint returns accessible."""
        config = _make_config(
            storage__backend=StorageBackend.MINIO,
            storage__s3_endpoint="http://localhost:9000",
            storage__s3_bucket="test-bucket",
        )
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            text, ok = _check_storage(config)
        assert text == "accessible"
        assert ok is True

    def test_s3_backend_unreachable(self) -> None:
        """MINIO backend with unreachable endpoint returns endpoint_unreachable."""
        config = _make_config(
            storage__backend=StorageBackend.MINIO,
            storage__s3_endpoint="http://localhost:9000",
            storage__s3_bucket="test-bucket",
        )
        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            text, ok = _check_storage(config)
        assert text == "endpoint_unreachable"
        assert ok is False

    def test_s3_backend_no_endpoint(self) -> None:
        """S3 backend with empty endpoint returns no_endpoint_configured."""
        config = _make_config(
            storage__backend=StorageBackend.S3,
            storage__s3_endpoint="",
            storage__s3_bucket="test-bucket",
            storage__s3_access_key="key",
            storage__s3_secret_key="secret",
        )
        text, ok = _check_storage(config)
        assert text == "no_endpoint_configured"
        assert ok is False


# ===================================================================
# 3. _check_gravitino()
# ===================================================================


class TestCheckGravitino:
    """Tests for _check_gravitino helper."""

    def test_reachable(self) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"[]"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            text, ok = _check_gravitino("http://gravitino:8090")
        assert text == "healthy"
        assert ok is True

    def test_unreachable(self) -> None:
        with patch("urllib.request.urlopen", side_effect=Exception("refused")):
            text, ok = _check_gravitino("http://gravitino:8090")
        assert text == "unreachable"
        assert ok is False


# ===================================================================
# 4. _check_lance_rest()
# ===================================================================


class TestCheckLanceRest:
    """Tests for _check_lance_rest helper."""

    def test_reachable(self) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"[]"
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        with patch("urllib.request.build_opener", return_value=mock_opener):
            text, ok = _check_lance_rest("http://lance-rest:9101/lance")
        assert text == "healthy"
        assert ok is True

    def test_unreachable(self) -> None:
        mock_opener = MagicMock()
        mock_opener.open.side_effect = Exception("refused")
        with patch("urllib.request.build_opener", return_value=mock_opener):
            text, ok = _check_lance_rest("http://lance-rest:9101/lance")
        assert text == "unreachable"
        assert ok is False


# ===================================================================
# 5. _check_ray()
# ===================================================================


class TestCheckRay:
    """Tests for _check_ray helper."""

    def test_reachable(self) -> None:
        mock_ray = MagicMock()
        mock_ray.is_initialized.return_value = False
        with patch.dict(sys.modules, {"ray": mock_ray}):
            text, ok = _check_ray("auto")
        assert text == "healthy"
        assert ok is True
        mock_ray.init.assert_called_once()

    def test_unreachable(self) -> None:
        mock_ray = MagicMock()
        mock_ray.is_initialized.side_effect = Exception("no ray")
        with patch.dict(sys.modules, {"ray": mock_ray}):
            text, ok = _check_ray("auto")
        assert text == "unreachable"
        assert ok is False


# ===================================================================
# 6. _check_redis()
# ===================================================================


class TestCheckRedis:
    """Tests for _check_redis helper."""

    def test_reachable(self) -> None:
        mock_redis_mod = MagicMock()
        mock_client = MagicMock()
        mock_redis_mod.from_url.return_value = mock_client
        with patch.dict(sys.modules, {"redis": mock_redis_mod}):
            text, ok = _check_redis("redis://localhost:6379/0")
        assert text == "healthy"
        assert ok is True
        mock_client.ping.assert_called_once()

    def test_unreachable(self) -> None:
        mock_redis_mod = MagicMock()
        mock_client = MagicMock()
        mock_client.ping.side_effect = Exception("connection refused")
        mock_redis_mod.from_url.return_value = mock_client
        with patch.dict(sys.modules, {"redis": mock_redis_mod}):
            text, ok = _check_redis("redis://localhost:6379/0")
        assert text == "unreachable"
        assert ok is False


# ===================================================================
# 7. /health/live
# ===================================================================


class TestHealthLive:
    """Tests for liveness probe."""

    @pytest.mark.asyncio
    async def test_returns_200_ok(self) -> None:
        app = _make_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/health/live")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ===================================================================
# 8. /health/ready
# ===================================================================


class TestHealthReady:
    """Tests for readiness probe."""

    @pytest.mark.asyncio
    async def test_ready_503_while_starting(self, tmp_path: object) -> None:
        """Before lifespan completes (app.state.ready False), readiness is 503.

        This gates traffic so a half-initialized worker never receives requests.
        """
        config = _make_config(
            storage__backend=StorageBackend.LOCAL,
            storage__base_uri=str(tmp_path),
        )
        app = create_app(config)
        app.state.lake = MagicMock()
        # Note: app.state.ready intentionally left unset (simulates startup).
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/health/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "starting"

    @pytest.mark.asyncio
    async def test_ready_storage_ok(self, tmp_path: object) -> None:
        config = _make_config(
            storage__backend=StorageBackend.LOCAL,
            storage__base_uri=str(tmp_path),
        )
        app = _make_app(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/health/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["storage"] == "accessible"

    @pytest.mark.asyncio
    async def test_ready_degraded_when_storage_fails(self) -> None:
        config = _make_config(
            storage__backend=StorageBackend.LOCAL,
            storage__base_uri="/tmp/__nonexistent_for_ready__",
        )
        app = _make_app(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["storage"] == "not_found"

    @pytest.mark.asyncio
    async def test_ready_includes_gravitino_when_enabled(self) -> None:
        config = _make_config(
            storage__backend=StorageBackend.LOCAL,
            storage__base_uri="/tmp/__nonexistent_ready_grav__",
            gravitino__enabled=True,
            gravitino__uri="http://gravitino:8090",
        )
        app = _make_app(config)
        with patch("urllib.request.urlopen", side_effect=Exception("refused")):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/health/ready")
        body = resp.json()
        assert body["gravitino"] == "unreachable"

    @pytest.mark.asyncio
    async def test_ready_includes_ray_when_configured(self) -> None:
        config = _make_config(
            storage__backend=StorageBackend.LOCAL,
            storage__base_uri="/tmp/__nonexistent_ready_ray__",
        )
        config.compute.ray_address = "auto"
        app = _make_app(config)
        mock_ray = MagicMock()
        mock_ray.is_initialized.side_effect = Exception("no ray")
        with patch.dict(sys.modules, {"ray": mock_ray}):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/health/ready")
        body = resp.json()
        assert body["ray"] == "unreachable"

    @pytest.mark.asyncio
    async def test_ready_includes_redis_when_enabled(self) -> None:
        config = _make_config(
            storage__backend=StorageBackend.LOCAL,
            storage__base_uri="/tmp/__nonexistent_ready_redis__",
        )
        config.redis.enabled = True
        config.redis.url = "redis://localhost:6379/0"
        app = _make_app(config)
        mock_redis_mod = MagicMock()
        mock_client = MagicMock()
        mock_client.ping.side_effect = Exception("refused")
        mock_redis_mod.from_url.return_value = mock_client
        with patch.dict(sys.modules, {"redis": mock_redis_mod}):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/health/ready")
        body = resp.json()
        assert body["redis"] == "unreachable"


# ===================================================================
# 9. /health (backward compatible)
# ===================================================================


class TestHealthBackwardCompatible:
    """Tests for legacy /health endpoint."""

    @pytest.mark.asyncio
    async def test_basic_health(self, tmp_path: object) -> None:
        config = _make_config(
            storage__backend=StorageBackend.LOCAL,
            storage__base_uri=str(tmp_path),
        )
        app = _make_app(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["storage"] == "accessible"

    @pytest.mark.asyncio
    async def test_gravitino_included_when_enabled(self) -> None:
        config = _make_config(
            storage__backend=StorageBackend.LOCAL,
            storage__base_uri="/tmp/__nonexistent_hc_grav__",
            gravitino__enabled=True,
            gravitino__uri="http://gravitino:8090",
        )
        app = _make_app(config)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"[]"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/health")
        body = resp.json()
        assert body["gravitino"] == "healthy"

    @pytest.mark.asyncio
    async def test_lance_rest_included_when_enabled(self) -> None:
        config = _make_config(
            storage__backend=StorageBackend.LOCAL,
            storage__base_uri="/tmp/__nonexistent_hc_lance__",
            gravitino__enabled=True,
            gravitino__uri="http://gravitino:8090",
            gravitino__lance_rest_enabled=True,
            gravitino__lance_rest_uri="http://lance-rest:9101/lance",
        )
        app = _make_app(config)
        # Mock gravitino check (urlopen) and lance rest check (build_opener)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"[]"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("urllib.request.build_opener", return_value=mock_opener):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/health")
        body = resp.json()
        assert body["lance_rest"] == "healthy"

    @pytest.mark.asyncio
    async def test_degraded_when_storage_unavailable(self) -> None:
        config = _make_config(
            storage__backend=StorageBackend.LOCAL,
            storage__base_uri="/tmp/__nonexistent_hc_deg__",
        )
        app = _make_app(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"


# ===================================================================
# 10. _attach_pool_stats()
# ===================================================================


class TestAttachPoolStats:
    """Tests for _attach_pool_stats helper."""

    def test_with_real_lake(self) -> None:
        """When lake has a real session manager, stats are attached.

        We create a non-MagicMock object so the isinstance(lake, MagicMock)
        check in _attach_pool_stats evaluates to False.
        """
        mock_stats = MagicMock()
        mock_stats.pool_size = 5
        mock_stats.active_sessions = 2
        mock_stats.queued_requests = 0
        mock_stats.total_queries = 100
        mock_stats.total_errors = 1

        mock_sm = MagicMock()
        mock_sm.get_stats.return_value = mock_stats

        # Use a plain object subclass so isinstance(..., MagicMock) is False
        class FakeLake:
            def get_session_manager(self) -> object:
                return mock_sm

        request = MagicMock()
        request.app.state.lake = FakeLake()

        status: dict = {}
        _attach_pool_stats(status, request)
        assert "duckdb_pool" in status
        assert status["duckdb_pool"]["pool_size"] == 5
        assert status["duckdb_pool"]["active_sessions"] == 2
        assert status["duckdb_pool"]["total_queries"] == 100

    def test_with_magic_mock_lake(self) -> None:
        """When lake is a plain MagicMock, it is skipped (isinstance check)."""
        request = MagicMock()
        request.app.state.lake = MagicMock()

        status: dict = {}
        _attach_pool_stats(status, request)
        assert "duckdb_pool" not in status

    def test_without_lake(self) -> None:
        """When app.state has no lake attribute, no stats are added."""
        request = MagicMock()
        request.app.state = MagicMock(spec=[])  # No 'lake' attribute

        status: dict = {}
        _attach_pool_stats(status, request)
        assert "duckdb_pool" not in status

    def test_lake_is_none(self) -> None:
        """When lake is None, no stats are added."""
        request = MagicMock()
        request.app.state.lake = None

        status: dict = {}
        _attach_pool_stats(status, request)
        assert "duckdb_pool" not in status

    def test_session_manager_exception_is_non_fatal(self) -> None:
        """When get_stats raises, the exception is silently swallowed."""

        class ErrorLake:
            def get_session_manager(self) -> object:
                raise RuntimeError("boom")

        request = MagicMock()
        request.app.state.lake = ErrorLake()

        status: dict = {"status": "ok"}
        _attach_pool_stats(status, request)
        # Should not crash; status unchanged
        assert "duckdb_pool" not in status
        assert status["status"] == "ok"


# ===================================================================
# 11. /metrics
# ===================================================================


class TestMetrics:
    """Tests for Prometheus metrics endpoint."""

    @pytest.mark.asyncio
    async def test_metrics_returns_200(self) -> None:
        """When prometheus_client is installed, return metrics."""
        app = _make_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/metrics")
        assert resp.status_code == 200
        assert "arrow_lake_" in resp.text

    @pytest.mark.asyncio
    async def test_metrics_503_when_no_prometheus(self) -> None:
        """When prometheus_client import fails, return 503.

        Block only prometheus_client in sys.modules; arrow_lake.core.metrics
        stays loaded so the middleware doesn't crash.
        """
        app = _make_app()
        saved_prom = sys.modules.pop("prometheus_client", None)
        try:
            # Setting to None forces a subsequent `import prometheus_client`
            # to raise ImportError during the endpoint's inline import.
            sys.modules["prometheus_client"] = None  # type: ignore[assignment]
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/metrics")
            assert resp.status_code == 503
            assert "prometheus_client not installed" in resp.text
        finally:
            sys.modules.pop("prometheus_client", None)
            if saved_prom is not None:
                sys.modules["prometheus_client"] = saved_prom
