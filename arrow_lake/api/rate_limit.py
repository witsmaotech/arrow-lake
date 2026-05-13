"""Rate limiting middleware for the Arrow Lake REST API.

Provides in-memory sliding-window rate limiting per (IP, path) pair.
Disabled by default — enable via RateLimitConfig.enabled = True.

Uses a fixed-window counter with asyncio.Lock for thread safety.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from arrow_lake.config import RateLimitConfig

_WINDOW_SECONDS = 60.0


class _Counter:
    """Thread-safe fixed-window request counter."""

    __slots__ = ("_lock", "_timestamps", "last_hit")

    def __init__(self) -> None:
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()
        self.last_hit: float = 0.0

    async def hit(self, now: float, window: float, limit: int = 0) -> tuple[bool, float]:
        """Record a request and return (allowed, earliest_timestamp) tuple."""
        async with self._lock:
            cutoff = now - window
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            if limit > 0 and len(self._timestamps) >= limit:
                self.last_hit = now
                return False, self._timestamps[0] if self._timestamps else now
            self._timestamps.append(now)
            self.last_hit = now
            return True, 0.0

    async def remaining(self, now: float, window: float, limit: int) -> int:
        """Return remaining requests in the current window."""
        async with self._lock:
            cutoff = now - window
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            return max(0, limit - len(self._timestamps))

    async def count(self, now: float, window: float) -> int:
        """Return current request count in the window."""
        async with self._lock:
            cutoff = now - window
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            return len(self._timestamps)


# Module-level counter storage shared across middleware instances.
_counters: dict[str, _Counter] = defaultdict(_Counter)
_last_cleanup = 0.0
_CLEANUP_INTERVAL = 120.0  # seconds


def _evict_stale_counters(now: float) -> None:
    """Remove counters that haven't been hit in over 2 window periods."""
    global _last_cleanup
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    cutoff = now - _WINDOW_SECONDS * 2
    stale = [k for k, v in _counters.items() if v.last_hit < cutoff]
    for k in stale:
        del _counters[k]


async def rate_limit_middleware_fn(
    request: Request,
    call_next,
    *,
    rpm: int = 60,
    burst: int = 10,
    exempt_paths: list[str] | None = None,
) -> Response:
    """Pure ASGI rate limiting middleware function.

    Apply rate limits per (client IP, path) pair.
    Uses in-memory fixed-window counters — suitable for single-process deployments.
    """
    path = request.url.path
    exempt_prefixes = tuple(exempt_paths or [])

    if any(path.startswith(prefix) for prefix in exempt_prefixes):
        return await call_next(request)

    if request.method == "OPTIONS":
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{path}"

    now = time.time()
    _evict_stale_counters(now)
    counter = _counters[key]

    allowed, earliest = await counter.hit(now, _WINDOW_SECONDS, limit=rpm)

    if not allowed:
        from arrow_lake.core.metrics import rate_limit_rejected_total

        rate_limit_rejected_total.labels(endpoint=path, path=path).inc()
        retry_after = int(_WINDOW_SECONDS - (now - earliest)) if earliest else 60

        return JSONResponse(
            status_code=429,
            content={
                "success": False,
                "error": "RATE_LIMIT_EXCEEDED",
                "message": "Too many requests. Please retry later.",
            },
            headers={"Retry-After": str(max(1, retry_after))},
        )

    response = await call_next(request)

    remaining = await counter.remaining(now, _WINDOW_SECONDS, rpm)
    response.headers["X-RateLimit-Limit"] = str(rpm)
    response.headers["X-RateLimit-Remaining"] = str(remaining)

    return response

def get_limiter(config: RateLimitConfig) -> bool:
    """Return whether rate limiting is enabled for the given config."""
    return config.enabled
