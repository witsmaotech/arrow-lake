"""Rate limiting middleware for the Arrow Lake REST API.

Provides in-memory sliding-window rate limiting per (IP, path) pair.
Disabled by default — enable via RateLimitConfig.enabled = True.

Uses a fixed-window counter with asyncio.Lock for thread safety.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
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
            # Evict expired timestamps
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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply rate limits per (client IP, path) pair.

    Configured via RateLimitConfig. Disabled when config.enabled is False.
    Exempt paths (health, metrics, docs) bypass rate limiting.

    Uses in-memory fixed-window counters — suitable for single-process
    deployments. For multi-instance setups, use a reverse proxy rate limiter.
    """

    def __init__(
        self,
        app,
        *,
        rpm: int = 60,
        burst: int = 10,
        exempt_paths: list[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._rpm = rpm
        self._burst = burst
        self._exempt_prefixes = tuple(exempt_paths or [])
        self._counters: dict[str, _Counter] = defaultdict(_Counter)
        self._lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Exempt paths bypass rate limiting
        if any(path.startswith(prefix) for prefix in self._exempt_prefixes):
            return await call_next(request)

        # OPTIONS preflight bypass (CORS)
        if request.method == "OPTIONS":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{path}"

        now = time.time()
        counter = self._counters[key]

        # Record the request and check limit atomically (avoids TOCTOU race)
        allowed = await counter.hit(now, _WINDOW_SECONDS, limit=self._rpm)

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

        # Add rate limit headers
        remaining = await counter.remaining(now, _WINDOW_SECONDS, self._rpm)
        response.headers["X-RateLimit-Limit"] = str(self._rpm)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response


def get_limiter(config: RateLimitConfig) -> RateLimitMiddleware | None:
    """Create a RateLimitMiddleware from config.

    Returns None if rate limiting is disabled.
    """
    if not config.enabled:
        return None

    return RateLimitMiddleware(
        app=None,  # type: ignore[arg-type]  # will be set by add_middleware
        rpm=config.default_requests_per_minute,
        burst=config.default_burst,
        exempt_paths=config.exempt_paths,
    )
