"""Redis-backed rate limiter + login lockout for multi-worker deployments.

v1.9.2 批5. Mirrors ``_redis_task_store`` fail-open semantics: when Redis is
unavailable, callers fall back to the in-memory paths in
``rate_limit.py`` / ``routers/auth.py`` (preserved).

Key layout::

    {prefix}hit:{ip}:{path}   →  INCR + EXPIRE fixed-window counter
    {prefix}{login_bucket}    →  ZSET of failure timestamps per (username:ip)
                                 member=`{username}:{ip}:{nonce}`, score=ts

The ZSET approach gives O(log N) sliding-window lockout with single-command
atomicity (ZADD / ZREMRANGEBYSCORE / ZCARD), matching the v1.9.1 thresholds
(10 failures / 15 min lockout).
"""

from __future__ import annotations

import logging
import time
from typing import Any

try:
    import redis as _redis_module
except ImportError:  # pragma: no cover
    _redis_module = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class RedisRateLimiter:
    """Redis-backed (ip, path) request counter and (username, ip) login lockout.

    Constructed once in ``app.py`` lifespan when ``config.redis.enabled`` and
    Redis is reachable; attached to ``app.state.redis_rate_limiter``. All
    public methods are best-effort: on any Redis error they flip
    ``_connected`` to False and let the caller fall back to the in-memory
    path. They never raise.
    """

    def __init__(
        self,
        *,
        key_prefix: str = "arrow_lake:rl:",
        login_bucket: str = "login",
        redis_url: str = "redis://localhost:6379/0",
        password: str = "",
        ssl: bool = False,
        pool_size: int = 10,
        login_fail_limit: int = 10,
        login_lockout_seconds: int = 900,
    ) -> None:
        self._prefix = key_prefix
        self._login_bucket = f"{key_prefix}{login_bucket}"
        self._login_fail_limit = login_fail_limit
        self._login_lockout_seconds = login_lockout_seconds
        self._redis: Any = None
        self._connected = False

        try:
            if _redis_module is None:
                raise ImportError("redis package not installed")

            kwargs: dict[str, Any] = {
                "max_connections": pool_size,
                "socket_timeout": 5,
                "socket_connect_timeout": 5,
                "decode_responses": True,
            }
            if password:
                kwargs["password"] = password
            if ssl:
                kwargs["ssl"] = True
                kwargs["ssl_cert_reqs"] = "required"

            self._redis = _redis_module.Redis.from_url(redis_url, **kwargs)
            self._redis.ping()
            self._connected = True
            logger.info("RedisRateLimiter connected: %s", redis_url)
        except Exception as exc:
            self._connected = False
            self._redis = None
            logger.warning("RedisRateLimiter: Redis unavailable (%s)", exc)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _ensure_connected(self) -> bool:
        """Lazy reconnect (P0-4, 2026-08-21).

        The old ``_handle_error`` only flipped ``_connected = False`` forever —
        the exact bug already fixed in ``_redis_task_store``: one Redis blip
        permanently degraded that worker to the in-memory counter (limit and
        login-lockout effectively ×4 across 4 gunicorn workers). Ping on each
        call while disconnected; the ping rides the existing 5s socket timeout.
        """
        if self._connected:
            return True
        if self._redis is None:
            return False
        try:
            self._redis.ping()
            self._connected = True
            logger.info("RedisRateLimiter reconnected")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # (ip, path) request counter — INCR + EXPIRE fixed window
    # ------------------------------------------------------------------

    def hit(self, ip: str, path: str, limit: int, window: int) -> tuple[bool, int, int] | None:
        """Record one request. Returns ``(allowed, remaining, retry_after)``.

        - ``allowed``: whether the request is under the limit.
        - ``remaining``: requests left in the current window (-1 if blocked).
        - ``retry_after``: seconds until the counter resets (0 if allowed).

        Returns ``None`` when Redis is unavailable so the caller falls back
        to the in-memory counter.
        """
        if not self._ensure_connected():
            return None
        key = f"{self._prefix}hit:{ip}:{path}"
        try:
            pipe = self._redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window, nx=True)  # only set TTL on first hit
            count, _ = pipe.execute()
            if count > limit:
                # ttl in seconds (ceil) for Retry-After
                ttl = self._redis.ttl(key)
                return False, 0, max(1, int(ttl)) if ttl and ttl > 0 else window
            return True, max(0, limit - int(count)), 0
        except Exception as exc:
            logger.debug("RedisRateLimiter.hit failed: %s", exc)
            self._handle_error()
            return None

    # ------------------------------------------------------------------
    # (username, ip) login lockout — ZSET sliding window
    # ------------------------------------------------------------------

    def check_login(self, username: str, ip: str) -> tuple[bool, int] | None:
        """Return ``(locked, failure_count)``.

        - ``locked``: True if failures >= limit within the lockout window.
        - ``failure_count``: recent failure count.

        Returns ``None`` when Redis unavailable (caller falls back).
        """
        if not self._ensure_connected():
            return None
        now = time.time()
        cutoff = now - self._login_lockout_seconds
        try:
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(self._login_bucket, 0, cutoff)
            pipe.zcard(self._login_bucket)
            _, count = pipe.execute()
            # Count is global across all (user,ip) pairs in the bucket; refine
            # by counting only members for this (username, ip) prefix. ZSET
            # members are namespaced "{username}:{ip}:{nonce}" so we can scan.
            members = self._redis.zrangebyscore(self._login_bucket, cutoff, now)
            key_prefix = f"{username}:{ip}:"
            n = sum(1 for m in members if m.startswith(key_prefix))
            return (n >= self._login_fail_limit), n
        except Exception as exc:
            logger.debug("RedisRateLimiter.check_login failed: %s", exc)
            self._handle_error()
            return None

    def record_login_failure(self, username: str, ip: str) -> None:
        """Record a login failure timestamp."""
        if not self._ensure_connected():
            return
        now = time.time()
        member = f"{username}:{ip}:{time.time_ns()}"  # nonce for uniqueness
        try:
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(self._login_bucket, 0, now - self._login_lockout_seconds)
            pipe.zadd(self._login_bucket, {member: now})
            pipe.expire(self._login_bucket, self._login_lockout_seconds)
            pipe.execute()
        except Exception as exc:
            logger.debug("RedisRateLimiter.record_login_failure failed: %s", exc)
            self._handle_error()

    def reset_login(self, username: str, ip: str) -> None:
        """Clear failures for a (username, ip) pair on successful login."""
        if not self._ensure_connected():
            return
        try:
            members = self._redis.zrange(self._login_bucket, 0, -1)
            key_prefix = f"{username}:{ip}:"
            to_remove = [m for m in members if m.startswith(key_prefix)]
            if to_remove:
                self._redis.zrem(self._login_bucket, *to_remove)
        except Exception as exc:
            logger.debug("RedisRateLimiter.reset_login failed: %s", exc)
            self._handle_error()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _handle_error(self) -> None:
        """Mark disconnected; caller falls back to in-memory."""
        self._connected = False

    def shutdown(self) -> None:
        if self._redis is not None:
            try:
                self._redis.close()
            except Exception:
                pass
            self._connected = False


def create_rate_limiter(redis_config: Any) -> RedisRateLimiter | None:
    """Factory: return a connected RedisRateLimiter, or None to fall back."""
    if not getattr(redis_config, "enabled", False):
        return None
    limiter = RedisRateLimiter(
        key_prefix=redis_config.rate_limit_key_prefix,
        login_bucket=redis_config.rate_limit_login_bucket,
        redis_url=redis_config.url,
        password=redis_config.password,
        ssl=redis_config.ssl,
        pool_size=redis_config.redis_pool_size,
    )
    if limiter.is_connected:
        return limiter
    logger.warning("Redis enabled but unreachable; rate_limit falls back to in-memory")
    return None
