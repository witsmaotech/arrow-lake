"""Per-dataset lance scan circuit breaker (v1.10.4).

When an opt-in dataset's native lance scan repeatedly enters uninterruptible
D-state IO (the watchdog daemonizes the worker via ``on_uninterruptible``),
this breaker trips and demotes that dataset back to ``pyarrow_fallback`` for a
cooldown window — collapsing the blast radius from "whole OLAP path wedges"
to "one opt-in dataset temporarily slower".

Design (see ``docs_offline/v1.10.4-version-plan.md`` §2.2):

- Cross-worker state lives in Redis (gunicorn runs N workers). Mirrors the
  lazy best-effort client pattern in ``arrow_lake._lake_ingest._embed_redis_client``
  so it works even when the task store is not initialized.
- **Fail-open**: a Redis outage disables tripping, *not* native scans. The
  per-query watchdog (``conn.interrupt`` + ``on_uninterruptible``) still bounds
  each acute failure to a single 504, so a Redis blip must not permanently
  shut down the performance path.
- The trip/cooldown logic talks to Redis via a small duck-typed surface
  (``incr``/``expire``/``exists``/``set``), so unit tests inject a dict-backed
  fake client with TTL semantics.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["LanceScanBreaker"]

# Redis key namespace: arrow-lake:lance_cb:{dataset}:trips | :cooldown
_KEY_PREFIX = "arrow-lake:lance_cb"


# --- Lazy best-effort Redis client (module-level singleton) ---
# Mirrors arrow_lake._lake_ingest._embed_redis_client(): prefers TaskManager's
# shared connection, falls back to a direct ARROW_LAKE__REDIS__URL connection.
# Cached after first attempt; returns None on any failure (fail-open).
_breaker_redis: Any = None
_breaker_redis_tried = False


def _breaker_redis_client() -> Any:
    """Best-effort lazy Redis client for cross-worker breaker state."""
    global _breaker_redis, _breaker_redis_tried
    try:
        from arrow_lake.api.tasks import TaskManager

        store = TaskManager._redis_store
        if store is not None and getattr(store, "_connected", False):
            return store._redis
    except Exception:
        pass
    if _breaker_redis_tried:
        return _breaker_redis
    _breaker_redis_tried = True
    try:
        import redis as _redis_mod

        url = os.environ.get("ARROW_LAKE__REDIS__URL", "")
        if not url:
            return None
        # socket_timeout bounds command latency (not just connect) so a slow Redis
        # can't stall the watchdog's on_uninterruptible callback (which runs on the
        # request thread right before raising the 504). Best-effort: any error → fail-open.
        _breaker_redis = _redis_mod.Redis.from_url(
            url, socket_connect_timeout=2, socket_timeout=2,
        )
        _breaker_redis.ping()
        return _breaker_redis
    except Exception:
        _breaker_redis = None
        return None


class LanceScanBreaker:
    """Demote a dataset from native lance scan after repeated D-state events.

    Args:
        threshold: D-state events within ``window_s`` that trip the breaker.
        window_s: Sliding window (seconds) for counting trips.
        cooldown_s: Seconds a tripped dataset stays demoted before retry.
        redis_client: Injected Redis-like client for tests. None → lazy connect.
    """

    def __init__(
        self,
        *,
        threshold: int = 2,
        window_s: int = 600,
        cooldown_s: int = 1800,
        redis_client: Any = None,
    ) -> None:
        self.threshold = max(1, int(threshold))
        self.window_s = int(window_s)
        self.cooldown_s = int(cooldown_s)
        self._redis_client = redis_client

    def _get_redis(self) -> Any:
        """Return the injected client, else the lazy module-level one (or None)."""
        if self._redis_client is not None:
            return self._redis_client
        return _breaker_redis_client()

    def record_trip(self, dataset: str) -> None:
        """Record one D-state strike; trip the breaker once threshold is reached.

        Fail-open: any Redis error is swallowed (the per-query watchdog still
        bounded the acute failure with a 504).
        """
        r = self._get_redis()
        if r is None:
            return
        try:
            trips_key = f"{_KEY_PREFIX}:{dataset}:trips"
            n = int(r.incr(trips_key))
            # (Re)start the sliding window each strike so the count reflects
            # "threshold events within window_s", not an unbounded lifetime total.
            r.expire(trips_key, self.window_s)
            if n >= self.threshold:
                # NX: set cooldown only once (fixed window from the first trip that
                # crossed threshold). Later strikes during cooldown must NOT extend
                # it, so a flaky dataset recovers on schedule instead of being
                # locked out indefinitely by continued (e.g. pyarrow-path) trips.
                r.set(
                    f"{_KEY_PREFIX}:{dataset}:cooldown", 1, ex=self.cooldown_s, nx=True,
                )
                logger.warning(
                    "lance_cb_tripped dataset=%s trips=%s threshold=%s "
                    "cooldown_s=%s — demoting native lance scan to pyarrow_fallback",
                    dataset, n, self.threshold, self.cooldown_s,
                )
        except Exception as exc:
            logger.debug(
                "lance_cb record_trip failed (fail-open) for %s: %s", dataset, exc,
            )

    def is_tripped(self, dataset: str) -> bool:
        """True if the dataset is in cooldown (should use pyarrow_fallback).

        Fail-open: any Redis error returns False (do not block native scans on a
        Redis outage).
        """
        r = self._get_redis()
        if r is None:
            return False
        try:
            return bool(r.exists(f"{_KEY_PREFIX}:{dataset}:cooldown"))
        except Exception as exc:
            logger.debug(
                "lance_cb is_tripped failed (fail-open) for %s: %s", dataset, exc,
            )
            return False
