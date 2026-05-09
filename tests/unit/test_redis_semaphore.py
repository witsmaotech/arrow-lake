"""Tests for RedisCountingSemaphore and create_semaphore factory."""

from __future__ import annotations

import threading
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
