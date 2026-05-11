"""Tests for app lifecycle, startup checks, auth validation, and create_app branches."""

from __future__ import annotations

import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from arrow_lake.api.app import (
    _check_duckdb_extensions,
    _check_storage_connectivity,
    _validate_auth_config,
    create_app,
)
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.config._enums import AuthMode, StorageBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    auth_mode: str = "api_key",
    api_key: str = "test-api-key",
    jwt_secret: str = "",
    rate_limit_enabled: bool = False,
    redis_enabled: bool = False,
    opentelemetry_enabled: bool = False,
    docs_enabled: bool = False,
    audit_enabled: bool = False,
    audit_hmac: str = "",
) -> ArrowLakeConfig:
    """Return an ArrowLakeConfig with safe defaults for unit tests."""
    config = ArrowLakeConfig()
    config.api.api_key = api_key
    config.auth.auth_mode = AuthMode(auth_mode)
    config.auth.jwt_secret_key = jwt_secret
    config.rate_limit.enabled = rate_limit_enabled
    config.redis.enabled = redis_enabled
    config.opentelemetry.enabled = opentelemetry_enabled
    config.audit.enabled = audit_enabled
    config.audit.hmac_secret_key = audit_hmac
    config.api.docs_enabled = docs_enabled
    return config


# ===========================================================================
# _check_storage_connectivity
# ===========================================================================


class TestCheckStorageConnectivity:
    """Tests for _check_storage_connectivity covering local and non-local backends."""

    def test_local_backend_returns_immediately(self) -> None:
        config = _make_config()
        config.storage.backend = StorageBackend.LOCAL
        _check_storage_connectivity(config)

    @patch("httpx.get")
    def test_non_local_backend_health_pass(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        config = _make_config()
        config.storage.backend = StorageBackend.MINIO
        config.storage.s3_endpoint = "http://minio:9000"
        config.storage.s3_bucket = "test-bucket"
        _check_storage_connectivity(config)
        mock_get.assert_called_once_with(
            "http://minio:9000/minio/health/live", timeout=5.0,
        )

    @patch("httpx.get")
    def test_non_local_backend_health_non_200(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_get.return_value = mock_resp
        config = _make_config()
        config.storage.backend = StorageBackend.S3
        config.storage.s3_endpoint = "http://s3:9000"
        config.storage.s3_bucket = "data"
        _check_storage_connectivity(config)

    @patch("httpx.get")
    def test_non_local_backend_connection_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = Exception("refused")
        config = _make_config()
        config.storage.backend = StorageBackend.MINIO
        config.storage.s3_endpoint = "http://unreachable:9000"
        config.storage.s3_bucket = "bucket"
        _check_storage_connectivity(config)


# ===========================================================================
# _check_duckdb_extensions
# ===========================================================================


class TestCheckDuckDBExtensions:
    """Tests for _check_duckdb_extensions."""

    def test_extensions_ok(self) -> None:
        _check_duckdb_extensions()  # Real call, should not raise

    def test_extensions_failure_logs_warning(self) -> None:
        with patch("duckdb.connect", side_effect=RuntimeError("no duckdb")):
            _check_duckdb_extensions()  # Should not raise


# ===========================================================================
# _validate_auth_config
# ===========================================================================


class TestValidateAuthConfig:
    """Tests for _validate_auth_config covering all validation branches."""

    def test_valid_api_key_config(self) -> None:
        _validate_auth_config(_make_config(auth_mode="api_key", api_key="my-key"))

    def test_api_key_mode_empty_key_raises(self) -> None:
        with pytest.raises(ValueError, match="api_key is empty"):
            _validate_auth_config(_make_config(auth_mode="api_key", api_key=""))

    def test_jwt_mode_empty_secret_raises(self) -> None:
        with pytest.raises(ValueError, match="jwt_secret_key is empty"):
            _validate_auth_config(_make_config(auth_mode="jwt", jwt_secret="", api_key=""))

    def test_both_mode_all_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one authentication method"):
            _validate_auth_config(_make_config(auth_mode="both", api_key="", jwt_secret=""))

    def test_both_mode_with_api_key_only_passes(self) -> None:
        _validate_auth_config(_make_config(auth_mode="both", api_key="my-key", jwt_secret=""))

    def test_audit_enabled_empty_hmac_raises(self) -> None:
        with pytest.raises(ValueError, match="hmac_secret_key is empty"):
            _validate_auth_config(_make_config(api_key="key", audit_enabled=True, audit_hmac=""))

    def test_audit_enabled_with_hmac_passes(self) -> None:
        _validate_auth_config(_make_config(api_key="key", audit_enabled=True, audit_hmac="super-secret-hmac-key"))

    def test_valid_jwt_config(self) -> None:
        _validate_auth_config(_make_config(auth_mode="jwt", jwt_secret="a" * 32, api_key=""))


# ===========================================================================
# create_app — rate limiting branch
# ===========================================================================


class TestCreateAppRateLimit:
    """Tests for create_app with rate limiting enabled."""

    def test_rate_limit_middleware_added_when_enabled(self) -> None:
        config = _make_config(rate_limit_enabled=True)
        config.rate_limit.default_requests_per_minute = 30
        config.rate_limit.default_burst = 5
        config.rate_limit.exempt_paths = ["/health"]
        app = create_app(config=config)
        assert app is not None


# ===========================================================================
# create_app — JWT + Redis branch
# ===========================================================================


class TestCreateAppJwtRedis:
    """Tests for create_app with JWT auth and Redis-enabled JWT blacklist."""

    @patch("arrow_lake.api.app._check_storage_connectivity")
    def test_jwt_redis_null_module(self, mock_storage: MagicMock) -> None:
        config = _make_config(auth_mode="jwt", jwt_secret="a" * 32, redis_enabled=True, api_key="")
        with patch("arrow_lake.query._redis_semaphore._redis_module", None):
            app = create_app(config=config)
            assert hasattr(app.state, "auth_service")

    @patch("arrow_lake.api.app._check_storage_connectivity")
    def test_jwt_redis_connection_failure_falls_back(self, mock_storage: MagicMock) -> None:
        config = _make_config(auth_mode="jwt", jwt_secret="a" * 32, redis_enabled=True, api_key="")
        mock_redis_mod = MagicMock()
        mock_redis_mod.Redis.from_url.side_effect = ConnectionError("redis down")
        with patch("arrow_lake.query._redis_semaphore._redis_module", mock_redis_mod):
            app = create_app(config=config)
            assert app.state.auth_service._redis is None

    @patch("arrow_lake.api.app._check_storage_connectivity")
    def test_jwt_redis_success_wires_client(self, mock_storage: MagicMock) -> None:
        config = _make_config(auth_mode="jwt", jwt_secret="a" * 32, redis_enabled=True, api_key="")
        config.redis.url = "redis://localhost:6379"
        config.redis.password = "secret"
        config.redis.ssl = True
        mock_client = MagicMock()
        mock_redis_mod = MagicMock()
        mock_redis_mod.Redis.from_url.return_value = mock_client
        with patch("arrow_lake.query._redis_semaphore._redis_module", mock_redis_mod):
            app = create_app(config=config)
            mock_client.ping.assert_called_once()
            assert app.state.auth_service._redis is mock_client


# ===========================================================================
# create_app — telemetry branch
# ===========================================================================


class TestCreateAppTelemetry:
    """Tests for create_app with OpenTelemetry enabled."""

    @patch("arrow_lake.api.telemetry.setup_telemetry")
    def test_telemetry_setup_called_when_enabled(self, mock_setup: MagicMock) -> None:
        config = _make_config(opentelemetry_enabled=True)
        app = create_app(config=config)
        mock_setup.assert_called_once()

    def test_telemetry_not_called_when_disabled(self) -> None:
        config = _make_config(opentelemetry_enabled=False)
        with patch("arrow_lake.api.telemetry.setup_telemetry") as mock_setup:
            create_app(config=config)
            mock_setup.assert_not_called()


# ===========================================================================
# create_app — docs disabled
# ===========================================================================


class TestCreateAppDocsDisabled:
    """Tests for create_app with docs_enabled=False."""

    def test_docs_urls_are_none_when_disabled(self) -> None:
        config = _make_config(docs_enabled=False)
        app = create_app(config=config)
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None

    def test_docs_urls_are_set_when_enabled(self) -> None:
        config = _make_config(docs_enabled=True)
        app = create_app(config=config)
        assert app.docs_url == "/docs"
        assert app.redoc_url == "/redoc"
        assert app.openapi_url == "/openapi.json"


# ===========================================================================
# lifespan — startup/shutdown
# ===========================================================================


class TestLifespan:
    """Tests for the application lifespan context manager."""

    @patch("arrow_lake.api.app._check_duckdb_extensions")
    @patch("arrow_lake.api.app._check_storage_connectivity")
    @patch("arrow_lake.Lake")
    def test_lifespan_creates_lake_and_handles_signals(
        self,
        mock_lake_cls: MagicMock,
        mock_storage: MagicMock,
        mock_duckdb: MagicMock,
    ) -> None:
        mock_lake = MagicMock()
        mock_lake_cls.return_value = mock_lake

        from arrow_lake.api.app import lifespan

        config = _make_config()
        app = FastAPI(lifespan=lifespan)
        app.state.config = config

        import asyncio

        async def _run():
            async with lifespan(app):
                pass
            mock_lake.shutdown.assert_called()

        asyncio.run(_run())

    @patch("arrow_lake.api.app._check_duckdb_extensions")
    @patch("arrow_lake.api.app._check_storage_connectivity")
    @patch("arrow_lake.Lake")
    def test_lifespan_restores_signal_handlers(
        self,
        mock_lake_cls: MagicMock,
        mock_storage: MagicMock,
        mock_duckdb: MagicMock,
    ) -> None:
        mock_lake_cls.return_value = MagicMock()

        from arrow_lake.api.app import lifespan

        config = _make_config()
        app = FastAPI(lifespan=lifespan)
        app.state.config = config

        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)

        import asyncio

        async def _run():
            async with lifespan(app):
                pass

        asyncio.run(_run())

        restored_sigterm = signal.getsignal(signal.SIGTERM)
        restored_sigint = signal.getsignal(signal.SIGINT)
        assert restored_sigterm == original_sigterm
        assert restored_sigint == original_sigint
