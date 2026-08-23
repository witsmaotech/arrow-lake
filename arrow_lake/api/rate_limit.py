"""Rate limiting middleware for the Arrow Lake REST API.

Provides in-memory sliding-window rate limiting per (IP, path) pair.
Enabled by default via RateLimitConfig.enabled = True.

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


def _peer_trusted(peer: str, trusted_proxies: set[str]) -> bool:
    """Exact-IP or CIDR membership check against the trusted proxy set.

    CIDR entries (e.g. ``172.18.0.0/16``) keep the config stable across docker
    network re-creates, where container IPs drift. Malformed entries are
    ignored (never widen trust for other peers, never crash a request).
    """
    if "*" in trusted_proxies or peer in trusted_proxies:
        return True
    import ipaddress

    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for entry in trusted_proxies:
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue  # exact-IP entries that are not valid networks
    return False


def _extract_client_ip(request: Request, trusted_proxies: set[str]) -> str:
    """Extract the real client IP — XFF only trusted from a trusted peer (v1.10.5 H2).

    X-Forwarded-For is client-controlled. It is honored ONLY when the direct
    peer (``request.client.host``) is itself a configured trusted proxy (or
    ``"*"`` explicitly opts in). With the default empty set, a direct client
    rotating a spoofed XFF no longer gets a fresh per-IP lockout/rate-limit
    bucket per attempt — the peer IP wins.

    Behind a reverse proxy, add its address (exact IP or CIDR) via
    ``ARROW_LAKE__RATE_LIMIT__TRUSTED_PROXIES`` (JSON array, e.g.
    ``["172.18.0.0/16"]``) to recover real client IPs.
    """
    peer = request.client.host if request.client else "unknown"
    if not _peer_trusted(peer, trusted_proxies):
        return peer
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        ips = [ip.strip() for ip in xff.split(",") if ip.strip()]
        # Walk right-to-left, skipping trusted proxies
        for ip in reversed(ips):
            if not _peer_trusted(ip, trusted_proxies):
                return ip
        # All IPs are trusted proxies — take leftmost
        if ips:
            return ips[0]
    return peer


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
    trusted_proxies: set[str] | None = None,
) -> Response:
    """Pure ASGI rate limiting middleware function.

    Apply rate limits per (client IP, path) pair.

    v1.9.2 批5: prefer Redis-backed counter (``app.state.redis_rate_limiter``)
    for multi-worker correctness; fall back to the in-memory ``_Counter`` when
    Redis is unavailable or returns None (fail-open). Thresholds/window match
    v1.9.1 (60s window, rpm requests/minute).
    """
    path = request.url.path
    exempt_prefixes = tuple(exempt_paths or [])

    if any(path.startswith(prefix) for prefix in exempt_prefixes):
        return await call_next(request)

    if request.method == "OPTIONS":
        return await call_next(request)

    client_ip = _extract_client_ip(request, trusted_proxies or set())
    key = f"{client_ip}:{path}"

    now = time.time()

    # ── Redis path (multi-worker) ──
    rl = getattr(request.app.state, "redis_rate_limiter", None)
    if rl is not None:
        # v1.10.7 WP3 (review H5): the sync redis pipeline must not run inline
        # — a slow/hung redis would block the event loop up to its 5s socket
        # timeout on EVERY request. Off-loop with a 100ms budget; a timeout
        # (or redis error → None) falls back to the in-memory counter.
        from arrow_lake.api.utils import run_sync

        try:
            res = await run_sync(
                rl.hit, client_ip, path,
                limit=rpm, window=int(_WINDOW_SECONDS),
                timeout=0.1, label="rl_hit",
            )
        except TimeoutError:
            res = None
        if res is not None:
            allowed, remaining, retry_after = res
            if not allowed:
                from arrow_lake.core.metrics import rate_limit_rejected_total

                rate_limit_rejected_total.labels(endpoint=path, path=path).inc()
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
            response.headers["X-RateLimit-Limit"] = str(rpm)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response
        # res is None → Redis hiccup → fall through to in-memory

    # ── In-memory fallback (single-process) ──
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
