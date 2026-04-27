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

    __slots__ = ("_lock", "_timestamps")

    def __init__(self) -> None:
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def hit(self, now: float, window: float, limit: int = 0) -> bool:
        """Record a request and return True if within limit, False if exceeded."""
        async with self._lock:
            cutoff = now - window
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            if limit > 0 and len(self._timestamps) >= limit:
                return False
            self._timestamps.append(now)
            return True

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
    counter = _counters[key]

    allowed = await counter.hit(now, _WINDOW_SECONDS, limit=rpm)

    if not allowed:
        from arrow_lake.core.metrics import rate_limit_rejected_total

        rate_limit_rejected_total.labels(endpoint=path, path=path).inc()
        retry_after = int(_WINDOW_SECONDS - (now - counter._timestamps[0])) if counter._timestamps else 60

        return JSONResponse(
            status_code=429,
            content={
                "success": False,
                "error": "RATE_LIMIT_EXCEEDED",
                "message": "Too many requests. Please retry later.",
            },
            headers={"Retry-After": str(retry_after)},
        )

    response = await call_next(request)

    remaining = await counter.remaining(now, _WINDOW_SECONDS, rpm)
    response.headers["X-RateLimit-Limit"] = str(rpm)
    response.headers["X-RateLimit-Remaining"] = str(remaining)

    return response

def get_limiter(config: RateLimitConfig) -> bool:
    """Return whether rate limiting is enabled for the given config."""
    return config.enabled
