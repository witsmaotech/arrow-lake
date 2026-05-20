"""Tests for RedisCountingSemaphore, InstanceRegistry, and create_semaphore factory."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch


def _make_redis_mock():
    """Create a mock redis module with Redis.from_url()."""
    mock_mod = MagicMock()
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_mod.Redis.from_url.return_value = mock_client
    return mock_mod, mock_client


class TestCreateSemaphore:
    """Factory function tests."""

    def test_returns_threading_semaphore_when_disabled(self) -> None:
        from arrow_lake.query._redis_semaphore import create_semaphore

        cfg = MagicMock(enabled=False)
        sem = create_semaphore(cfg, 4)
        assert isinstance(sem, threading.Semaphore)

    def test_returns_redis_semaphore_when_enabled(self) -> None:
        from arrow_lake.query._redis_semaphore import create_semaphore

        cfg = MagicMock(
            enabled=True,
            semaphore_key_prefix="test:",
            url="redis://localhost:6379/0",
            password="",
            ssl=False,
            semaphore_ttl_seconds=300,
            redis_pool_size=2,
        )
        with patch("arrow_lake.query._redis_semaphore._redis_module") as mock_mod:
            mock_client = MagicMock()
            mock_client.ping.return_value = True
            mock_mod.Redis.from_url.return_value = mock_client
            sem = create_semaphore(cfg, 4)
            from arrow_lake.query._redis_semaphore import RedisCountingSemaphore

            assert isinstance(sem, RedisCountingSemaphore)


class TestRedisCountingSemaphoreFallback:
    """Test fallback behavior when Redis is unavailable."""

    def test_acquire_falls_back_to_threading(self) -> None:
        from arrow_lake.query._redis_semaphore import RedisCountingSemaphore

        mock_mod, mock_client = _make_redis_mock()
        mock_client.ping.side_effect = Exception("no redis")
        with patch("arrow_lake.query._redis_semaphore._redis_module", mock_mod):
            sem = RedisCountingSemaphore("test:key", 2, "redis://localhost:6379/0")
            assert sem._connected is False
            assert sem.acquire(timeout=1.0) is True
            sem.release()

    def test_release_falls_back_to_threading(self) -> None:
        from arrow_lake.query._redis_semaphore import RedisCountingSemaphore

        mock_mod, mock_client = _make_redis_mock()
        mock_client.ping.side_effect = Exception("no redis")
        with patch("arrow_lake.query._redis_semaphore._redis_module", mock_mod):
            sem = RedisCountingSemaphore("test:key", 1, "redis://localhost:6379/0")
            sem.acquire(timeout=1.0)
            sem.release()
            assert sem._fallback._value == 1

    def test_timeout_returns_false(self) -> None:
        from arrow_lake.query._redis_semaphore import RedisCountingSemaphore

        mock_mod, mock_client = _make_redis_mock()
        mock_client.ping.side_effect = Exception("no redis")
        with patch("arrow_lake.query._redis_semaphore._redis_module", mock_mod):
            sem = RedisCountingSemaphore("test:key", 1, "redis://localhost:6379/0")
            sem.acquire(timeout=0.1)
            result = sem.acquire(timeout=0.1)
            assert result is False

    def test_shutdown_cleans_up(self) -> None:
        from arrow_lake.query._redis_semaphore import RedisCountingSemaphore

        mock_mod, _mock_client = _make_redis_mock()
        with patch("arrow_lake.query._redis_semaphore._redis_module", mock_mod):
            sem = RedisCountingSemaphore("test:key", 2, "redis://localhost:6379/0")
            assert sem._connected is True
            sem.shutdown()
            assert sem._connected is False


class TestRedisCountingSemaphoreWithFakeredis:
    """Test with fakeredis for realistic Redis behavior."""

    def test_acquire_release_cycle(self) -> None:
        from arrow_lake.query._redis_semaphore import RedisCountingSemaphore

        mock_mod, mock_client = _make_redis_mock()
        mock_client.eval.side_effect = [1, 1, 1]  # acquire succeeds, release succeeds
        mock_client.get.return_value = b"1"

        with patch("arrow_lake.query._redis_semaphore._redis_module", mock_mod):
            sem = RedisCountingSemaphore("test:cycle", 2, "redis://fake/0")

            assert sem.acquire(timeout=1.0) is True
            stats = sem.get_stats()
            assert stats.available_permits == 1
            assert stats.redis_connected is True

            sem.release()
            mock_client.get.return_value = b"0"
            stats = sem.get_stats()
            assert stats.available_permits == 2

    def test_exhausted_permits_block(self) -> None:
        import fakeredis
        from arrow_lake.query._redis_semaphore import RedisCountingSemaphore

        fake = fakeredis.FakeRedis()
        mock_mod = MagicMock()
        mock_mod.Redis.from_url.return_value = fake

        with patch("arrow_lake.query._redis_semaphore._redis_module", mock_mod):
            sem = RedisCountingSemaphore("test:exhaust", 1, "redis://fake/0")

            assert sem.acquire(timeout=0.2) is True
            result = sem.acquire(timeout=0.2)
            assert result is False

    def test_concurrent_acquire(self) -> None:
        import fakeredis
        from arrow_lake.query._redis_semaphore import RedisCountingSemaphore

        fake = fakeredis.FakeRedis()
        mock_mod = MagicMock()
        mock_mod.Redis.from_url.return_value = fake

        with patch("arrow_lake.query._redis_semaphore._redis_module", mock_mod):
            sem = RedisCountingSemaphore("test:concurrent", 2, "redis://fake/0")

            acquired = []
            barrier = threading.Barrier(3, timeout=5)

            def worker():
                barrier.wait()
                if sem.acquire(timeout=2.0):
                    acquired.append(True)

            threads = [threading.Thread(target=worker) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            assert len(acquired) == 2


# ---------------------------------------------------------------------------
# InstanceRegistry tests
# ---------------------------------------------------------------------------


class TestCreateInstanceRegistry:
    """Factory function tests for create_instance_registry."""

    def test_returns_none_when_redis_disabled(self) -> None:
        from arrow_lake.query._redis_semaphore import create_instance_registry

        cfg = MagicMock(enabled=False)
        result = create_instance_registry(cfg)
        assert result is None

    def test_returns_registry_when_enabled(self) -> None:
        from arrow_lake.query._redis_semaphore import (
            InstanceRegistry,
            create_instance_registry,
        )

        cfg = MagicMock(
            enabled=True,
            instance_registry_key="test:instances",
            url="redis://localhost:6379/0",
            password="",
            ssl=False,
            instance_heartbeat_ttl_seconds=30,
            redis_pool_size=2,
        )
        with patch("arrow_lake.query._redis_semaphore._redis_module") as mock_mod:
            mock_client = MagicMock()
            mock_client.ping.return_value = True
            mock_mod.Redis.from_url.return_value = mock_client
            registry = create_instance_registry(cfg)
            assert isinstance(registry, InstanceRegistry)


class TestInstanceRegistryStandalone:
    """Test InstanceRegistry fallback when Redis is unavailable."""

    def test_register_succeeds_without_redis(self) -> None:
        from arrow_lake.query._redis_semaphore import InstanceRegistry

        with patch("arrow_lake.query._redis_semaphore._redis_module") as mock_mod:
            mock_mod.Redis.from_url.side_effect = Exception("no redis")
            reg = InstanceRegistry("test:inst")
            assert reg.is_connected is False
            assert reg.register() is True

    def test_discover_count_is_1_without_redis(self) -> None:
        from arrow_lake.query._redis_semaphore import InstanceRegistry

        with patch("arrow_lake.query._redis_semaphore._redis_module") as mock_mod:
            mock_mod.Redis.from_url.side_effect = Exception("no redis")
            reg = InstanceRegistry("test:inst")
            assert reg.discover_instance_count() == 1

    def test_discover_instances_returns_self_without_redis(self) -> None:
        from arrow_lake.query._redis_semaphore import InstanceRegistry

        with patch("arrow_lake.query._redis_semaphore._redis_module") as mock_mod:
            mock_mod.Redis.from_url.side_effect = Exception("no redis")
            reg = InstanceRegistry("test:inst")
            instances = reg.discover_instances()
            assert instances == [reg.instance_id]

    def test_deregister_safe_without_redis(self) -> None:
        from arrow_lake.query._redis_semaphore import InstanceRegistry

        with patch("arrow_lake.query._redis_semaphore._redis_module") as mock_mod:
            mock_mod.Redis.from_url.side_effect = Exception("no redis")
            reg = InstanceRegistry("test:inst")
            reg.deregister()  # should not raise


class TestInstanceRegistryWithRedis:
    """Test InstanceRegistry with mock Redis."""

    def _make_registry(self) -> tuple:
        from arrow_lake.query._redis_semaphore import InstanceRegistry

        with patch("arrow_lake.query._redis_semaphore._redis_module") as mock_mod:
            mock_client = MagicMock()
            mock_client.ping.return_value = True
            mock_mod.Redis.from_url.return_value = mock_client
            reg = InstanceRegistry("test:instances", heartbeat_ttl_seconds=30)
            return reg, mock_client

    def test_register_adds_to_sorted_set(self) -> None:
        reg, mock_client = self._make_registry()
        mock_client.zcard.return_value = 1
        assert reg.register() is True
        mock_client.zadd.assert_called_once()
        call_args = mock_client.zadd.call_args
        assert call_args[0][0] == "test:instances"

    def test_deregister_removes_from_sorted_set(self) -> None:
        reg, mock_client = self._make_registry()
        reg.register()
        reg.deregister()
        mock_client.zrem.assert_called_once_with("test:instances", reg.instance_id)

    def test_discover_count_queries_redis(self) -> None:
        reg, mock_client = self._make_registry()
        mock_client.zcard.return_value = 3
        assert reg.discover_instance_count() == 3

    def test_discover_instances_returns_member_list(self) -> None:
        reg, mock_client = self._make_registry()
        mock_client.zrange.return_value = [b"abc123", b"def456"]
        instances = reg.discover_instances()
        assert instances == ["abc123", "def456"]

    def test_heartbeat_thread_started_on_register(self) -> None:
        reg, mock_client = self._make_registry()
        mock_client.zcard.return_value = 1
        reg.register()
        assert reg._heartbeat_thread is not None
        assert reg._heartbeat_thread.is_alive()
        reg.deregister()

    def test_shutdown_deregisters_and_closes(self) -> None:
        reg, mock_client = self._make_registry()
        reg.register()
        reg.shutdown()
        mock_client.zrem.assert_called()
        mock_client.close.assert_called()
        assert reg.is_connected is False

    def test_prune_expired_called_on_discover(self) -> None:
        reg, mock_client = self._make_registry()
        mock_client.zcard.return_value = 2
        reg.discover_instance_count()
        mock_client.zremrangebyscore.assert_called_once()

    def test_instance_id_is_unique(self) -> None:
        reg1, _ = self._make_registry()
        reg2, _ = self._make_registry()
        assert reg1.instance_id != reg2.instance_id


class TestSessionManagerInstanceRegistry:
    """Test DuckDBSessionManager integration with InstanceRegistry."""

    def test_from_config_registers_instance(self) -> None:
        from arrow_lake.config import OlapConfig, RedisConfig
        from arrow_lake.query.session_manager import DuckDBSessionManager

        redis_cfg = RedisConfig(enabled=False)
        olap_cfg = OlapConfig()
        mgr = DuckDBSessionManager.from_config(
            olap_config=olap_cfg,
            redis_config=redis_cfg,
        )
        stats = mgr.get_stats()
        assert stats.instance_count == 1
        assert stats.total_capacity == olap_cfg.max_concurrent_queries
        mgr.shutdown()

    def test_from_config_with_redis_registers_cluster(self) -> None:
        from arrow_lake.config import OlapConfig
        from arrow_lake.query.session_manager import DuckDBSessionManager

        redis_cfg = MagicMock(
            enabled=True,
            semaphore_key_prefix="test:",
            url="redis://localhost:6379/0",
            password="",
            ssl=False,
            semaphore_ttl_seconds=300,
            redis_pool_size=2,
            instance_registry_key="test:instances",
            instance_heartbeat_ttl_seconds=30,
        )

        with patch("arrow_lake.query._redis_semaphore._redis_module") as mock_mod:
            mock_client = MagicMock()
            mock_client.ping.return_value = True
            mock_client.zcard.return_value = 2
            mock_mod.Redis.from_url.return_value = mock_client

            mgr = DuckDBSessionManager.from_config(
                olap_config=OlapConfig(),
                redis_config=redis_cfg,
            )
            stats = mgr.get_stats()
            assert stats.instance_count == 2
            assert stats.total_capacity == 2 * OlapConfig().max_concurrent_queries
            mgr.shutdown()

    def test_shutdown_deregisters_from_cluster(self) -> None:
        from arrow_lake.config import OlapConfig
        from arrow_lake.query.session_manager import DuckDBSessionManager

        redis_cfg = MagicMock(
            enabled=True,
            semaphore_key_prefix="test:",
            url="redis://localhost:6379/0",
            password="",
            ssl=False,
            semaphore_ttl_seconds=300,
            redis_pool_size=2,
            instance_registry_key="test:instances",
            instance_heartbeat_ttl_seconds=30,
        )

        with patch("arrow_lake.query._redis_semaphore._redis_module") as mock_mod:
            mock_client = MagicMock()
            mock_client.ping.return_value = True
            mock_client.zcard.return_value = 1
            mock_mod.Redis.from_url.return_value = mock_client

            mgr = DuckDBSessionManager.from_config(
                olap_config=OlapConfig(),
                redis_config=redis_cfg,
            )
            mgr.shutdown()
            # After shutdown, registry should be cleared
            assert mgr._instance_registry is None
