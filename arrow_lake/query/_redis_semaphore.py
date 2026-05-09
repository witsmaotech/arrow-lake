"""Redis-backed counting semaphore for distributed DuckDB session coordination.

When Redis is disabled or unavailable, falls back to ``threading.Semaphore``.
Uses synchronous ``redis.Redis`` client with Lua scripts for atomic acquire/release.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any

from pydantic import BaseModel

try:
    import redis as _redis_module
except ImportError:  # pragma: no cover
    _redis_module = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class SemaphoreStats(BaseModel):
    """Snapshot of distributed semaphore state."""

    available_permits: int
    total_permits: int
    redis_connected: bool


class RedisCountingSemaphore:
    """Distributed counting semaphore backed by Redis.

    Uses Lua scripts for atomic acquire (INCR with cap) and release (DECR with floor).
    All operations are synchronous to match ``DuckDBSessionManager.acquire()``.
    """

    _LUA_ACQUIRE = """
local key = KEYS[1]
local max_permits = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local current = tonumber(redis.call('GET', key) or '0')
if current < max_permits then
    local new_val = redis.call('INCR', key)
    if ttl > 0 and new_val == 1 then
        redis.call('EXPIRE', key, ttl)
    end
    return 1
end
return 0
"""

    _LUA_RELEASE = """
local key = KEYS[1]
local min_val = 0
local current = tonumber(redis.call('GET', key) or '0')
if current > min_val then
    redis.call('DECR', key)
    return 1
end
return 0
"""

    def __init__(
        self,
        key: str,
        max_permits: int,
        redis_url: str = "redis://localhost:6379/0",
        *,
        password: str = "",
        ssl: bool = False,
        ttl_seconds: int = 300,
        pool_size: int = 10,
    ) -> None:
        self._key = key
        self._max_permits = max_permits
        self._ttl_seconds = ttl_seconds
        self._redis: Any = None
        self._connected = False
        self._redis_url = redis_url
        self._redis_kwargs: dict[str, Any] = {}
        self._acquired_via_redis = threading.local()

        try:
            if _redis_module is None:
                raise ImportError("redis package not installed")

            kwargs: dict[str, Any] = {
                "max_connections": pool_size,
                "socket_timeout": 5,
                "socket_connect_timeout": 5,
            }
            if password:
                kwargs["password"] = password
            if ssl:
                kwargs["ssl"] = True
                kwargs["ssl_cert_reqs"] = "required"

            self._redis_kwargs = kwargs
            self._redis = _redis_module.Redis.from_url(redis_url, **kwargs)
            self._redis.ping()
            self._connected = True
            logger.info("Redis semaphore connected: %s (permits=%d)", redis_url, max_permits)
        except Exception:
            self._connected = False
            self._redis = None
            logger.warning("Redis unavailable, falling back to threading.Semaphore")

        self._fallback = threading.Semaphore(max_permits)
        self._lock = threading.Lock()

    def acquire(self, timeout: float | None = None) -> bool:
        """Acquire a permit, blocking until one is available or timeout."""
        if self._connected and self._redis is not None:
            ok = self._redis_acquire(timeout)
            if ok:
                self._acquired_via_redis.v = True
            return ok
        ok = self._fallback_acquire(timeout)
        if ok:
            self._acquired_via_redis.v = False
        return ok

    def release(self) -> None:
        """Release a permit back to the semaphore."""
        acquired_redis = getattr(self._acquired_via_redis, "v", False)
        if acquired_redis and self._connected and self._redis is not None:
            self._redis_release()
        else:
            self._fallback.release()

    def get_stats(self) -> SemaphoreStats:
        """Return current semaphore state."""
        if self._connected and self._redis is not None:
            try:
                raw = self._redis.get(self._key)
                used = int(raw or 0)
                return SemaphoreStats(
                    available_permits=self._max_permits - used,
                    total_permits=self._max_permits,
                    redis_connected=True,
                )
            except Exception:
                return SemaphoreStats(
                    available_permits=self._max_permits,
                    total_permits=self._max_permits,
                    redis_connected=False,
                )
        return SemaphoreStats(
            available_permits=self._max_permits - (self._max_permits - self._fallback._value),
            total_permits=self._max_permits,
            redis_connected=False,
        )

    def shutdown(self) -> None:
        """Clean up Redis connection."""
        if self._redis is not None:
            with contextlib.suppress(Exception):
                self._redis.close()
            self._connected = False

    def _redis_acquire(self, timeout: float | None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        interval = 0.05
        while True:
            try:
                acquired = self._redis.eval(
                    self._LUA_ACQUIRE, 1, self._key, self._max_permits, self._ttl_seconds
                )
                if acquired:
                    return True
            except Exception:
                self._handle_redis_error()
                return self._fallback_acquire(timeout)

            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(interval)
            interval = min(interval * 1.5, 0.5)

    def _redis_release(self) -> None:
        try:
            self._redis.eval(self._LUA_RELEASE, 1, self._key)
        except Exception:
            self._handle_redis_error()
            # Do NOT release fallback — permit was acquired via Redis, not fallback

    def _fallback_acquire(self, timeout: float | None) -> bool:
        return self._fallback.acquire(timeout=timeout)

    def _handle_redis_error(self) -> None:
        with self._lock:
            if self._connected:
                self._connected = False
                logger.warning("Redis connection lost, falling back to threading.Semaphore")
        self._try_reconnect()

    def _try_reconnect(self) -> None:
        """Attempt to reconnect to Redis on the next operation."""
        if self._redis is None or _redis_module is None:
            return
        try:
            self._redis.ping()
            with self._lock:
                self._connected = True
            logger.info("Redis semaphore reconnected")
        except Exception:
            pass


def create_semaphore(
    redis_config: Any,
    max_permits: int,
) -> threading.Semaphore | RedisCountingSemaphore:
    """Factory: return RedisCountingSemaphore if enabled, else threading.Semaphore."""
    if getattr(redis_config, "enabled", False):
        return RedisCountingSemaphore(
            key=redis_config.semaphore_key_prefix + "duckdb",
            max_permits=max_permits,
            redis_url=redis_config.url,
            password=redis_config.password,
            ssl=redis_config.ssl,
            ttl_seconds=redis_config.semaphore_ttl_seconds,
            pool_size=redis_config.redis_pool_size,
        )
    return threading.Semaphore(max_permits)
