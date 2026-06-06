"""Phase 2 tests — exceptions, circuit breaker, fallback, config, logging, HTTP.

Consolidated test file covering all Phase 2 changes.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 2.1 Exception hierarchy
# ---------------------------------------------------------------------------


class TestNewExceptions:
    """ConcurrencyError, TransientError, ConsistencyError exist and work."""

    def test_concurrency_error_is_arrow_lake_error(self) -> None:
        from arrow_lake.exceptions import ArrowLakeError, ConcurrencyError, ErrorCode

        exc = ConcurrencyError(ErrorCode.STORAGE_LOCK_TIMEOUT, "lock timeout")
        assert isinstance(exc, ArrowLakeError)
        assert exc.error_code == ErrorCode.STORAGE_LOCK_TIMEOUT

    def test_transient_error_is_arrow_lake_error(self) -> None:
        from arrow_lake.exceptions import ArrowLakeError, ErrorCode, TransientError

        exc = TransientError(ErrorCode.TRANSIENT_NETWORK_ERROR, "network blip")
        assert isinstance(exc, ArrowLakeError)

    def test_consistency_error_is_arrow_lake_error(self) -> None:
        from arrow_lake.exceptions import ArrowLakeError, ConsistencyError, ErrorCode

        exc = ConsistencyError(ErrorCode.CACHE_STALE, "stale cache")
        assert isinstance(exc, ArrowLakeError)

    def test_new_error_codes_exist(self) -> None:
        from arrow_lake.exceptions import ErrorCode

        codes = [
            ErrorCode.STORAGE_LOCK_TIMEOUT,
            ErrorCode.CONCURRENT_MODIFICATION,
            ErrorCode.RESOURCE_EXHAUSTED,
            ErrorCode.TRANSIENT_NETWORK_ERROR,
            ErrorCode.TRANSIENT_RATE_LIMITED,
            ErrorCode.CACHE_STALE,
            ErrorCode.METADATA_CONFLICT,
        ]
        assert all(c.value for c in codes)


# ---------------------------------------------------------------------------
# 2.2 Resource limits config
# ---------------------------------------------------------------------------


class TestResourceLimitsConfig:
    """ResourceLimits and BackpressureConfig defaults."""

    def test_resource_limits_defaults(self) -> None:
        from arrow_lake.config import ResourceLimits

        r = ResourceLimits()
        assert r.max_query_time_seconds == 300
        assert r.max_concurrent_queries == 10
        assert r.max_result_rows == 1_000_000

    def test_backpressure_defaults(self) -> None:
        from arrow_lake.config import BackpressureConfig

        b = BackpressureConfig()
        assert b.ingest_queue_size == 10_000
        assert b.rejection_threshold == 0.9
        assert b.retry_max_attempts == 3

    def test_custom_values(self) -> None:
        from arrow_lake.config import ResourceLimits

        r = ResourceLimits(max_query_time_seconds=600, max_concurrent_queries=20)
        assert r.max_query_time_seconds == 600


# ---------------------------------------------------------------------------
# 2.3 Circuit breaker half-open race fix
# ---------------------------------------------------------------------------


class TestCircuitBreakerHalfOpen:
    """Half-open failure recovers counter slot."""

    def test_half_open_failure_decrements_counter(self) -> None:
        from arrow_lake.core.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(name="test", failure_threshold=3, half_open_max_calls=2)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

        with patch("time.monotonic", return_value=cb._last_failure_time + 100):
            assert cb.allow_request() is True

        assert cb._half_open_calls == 1
        cb.record_failure()
        assert cb._half_open_calls == 0
        assert cb._state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# 2.6 Structlog + HTTP
# ---------------------------------------------------------------------------


class TestStructlogExcInfo:
    """format_exc_info processor is configured."""

    def test_configure_logging_runs(self) -> None:
        from arrow_lake.core.logging import configure_logging

        configure_logging("INFO")  # Should not raise


class TestHttpClientDefaults:
    """HTTP clients have default timeout and limits."""

    def test_sync_client_has_defaults(self) -> None:
        import httpx
        from arrow_lake.core.http import create_http_client

        client = create_http_client()
        assert isinstance(client.timeout, httpx.Timeout)
        client.close()

    def test_async_client_has_defaults(self) -> None:
        import httpx
        from arrow_lake.core.http import create_async_http_client

        client = create_async_http_client()
        assert isinstance(client, httpx.AsyncClient)
        assert isinstance(client.timeout, httpx.Timeout)


# ---------------------------------------------------------------------------
# 2.4+2.7 Encoder sharding + fallback
# ---------------------------------------------------------------------------


class TestEmbeddingSharding:
    """encode_batch_sharded processes in shards with fault tolerance."""

    def test_shard_method_exists(self) -> None:
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder

        assert hasattr(ApiEmbeddingEncoder, "encode_batch_sharded")

    def test_small_batch_no_sharding(self) -> None:
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder

        encoder = ApiEmbeddingEncoder.__new__(ApiEmbeddingEncoder)
        mock_batch = MagicMock()
        mock_batch.embeddings = MagicMock()
        encoder.encode = MagicMock(return_value=mock_batch)

        result = encoder.encode_batch_sharded(["text1", "text2"], shard_size=100)
        assert result is mock_batch.embeddings


# ---------------------------------------------------------------------------
# 2.4 Storage lock timeout
# ---------------------------------------------------------------------------


class TestStorageLockTimeout:
    """Lock acquisition uses timeout and raises ConcurrencyError."""

    def test_acquire_lock_timeout_raises(self) -> None:
        from arrow_lake.exceptions import ConcurrencyError
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager.__new__(LanceStorageManager)
        storage._dataset_locks = {}
        storage._dataset_lock_max = 100

        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False
        storage._dataset_lock = MagicMock(return_value=mock_lock)

        with pytest.raises(ConcurrencyError, match="Lock acquisition timed out"):
            storage._acquire_dataset_lock("test_ds", timeout=0.01)

    def test_cleanup_partial_exists(self) -> None:
        from arrow_lake.ingest.storage import LanceStorageManager

        assert hasattr(LanceStorageManager, "cleanup_partial")


# ---------------------------------------------------------------------------
# 2.5 Session pool health
# ---------------------------------------------------------------------------


class TestSessionPoolHealth:
    def test_health_check_exists(self) -> None:
        from arrow_lake.query.session_manager import DuckDBSessionManager

        assert hasattr(DuckDBSessionManager, "_health_check")


# ---------------------------------------------------------------------------
# 2.8 Backup + Rollback
# ---------------------------------------------------------------------------


class TestBackupRestoreOrdering:
    def test_restore_method_exists(self) -> None:
        from arrow_lake.ops.backup_restore import BackupRestorer

        assert hasattr(BackupRestorer, "_restore_lance_dataset_remote")


class TestRollbackSafetyNet:
    def test_rollback_method_exists(self) -> None:
        from arrow_lake.workflow.rollback import StateRollback

        assert hasattr(StateRollback, "rollback")
