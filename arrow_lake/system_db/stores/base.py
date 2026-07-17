"""Store protocol + degradation primitives shared by all control-plane stores.

Two cross-cutting concerns every store needs:

* :data:`FailMode` — what to do when the system DB is unreachable
  (fail-close for security data, fail-soft for best-effort data).
* :class:`TTLCache` — short per-worker cache so hot paths (ACL checks)
  don't hit sqld on every request; writes invalidate locally, and the short
  TTL bounds cross-worker staleness to an acceptable window.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol, runtime_checkable


class FailMode:
    """Degradation strategy when the system database is unavailable.

    * ``FAIL_CLOSE`` — security path (RBAC / identity): refuse the request.
      Better to deny than to let an unauthenticated action through.
    * ``FAIL_SOFT`` — best-effort data (catalog / tasks / RAG sessions):
      log and fall back to the prior in-memory behavior.
    * ``FAIL_BACKFILL`` — derived index (lineage): query degrades to the
      source scan, new events are skipped until recovery, then backfilled.
    """

    FAIL_CLOSE = "fail_close"
    FAIL_SOFT = "fail_soft"
    FAIL_BACKFILL = "fail_backfill"


@runtime_checkable
class SystemStore(Protocol):
    """Marker protocol for all system_db stores."""

    fail_mode: str


class TTLCache:
    """Tiny thread-safe per-worker TTL cache.

    Used by stores to avoid a round-trip to sqld on every request (e.g. ACL
    resolution). Entries expire after ``ttl_seconds``; mutators call
    :meth:`invalidate` on write to keep the local worker fresh. Cross-worker
    consistency is bounded by the TTL (5s default — acceptable for control
    plane). A TTL of 0 disables caching entirely (tests).
    """

    def __init__(self, ttl_seconds: float = 5.0) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    @property
    def ttl(self) -> float:
        return self._ttl

    def get(self, key: str) -> Any | None:
        if self._ttl <= 0:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.monotonic() - ts > self._ttl:
                self._entries.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        if self._ttl <= 0:
            return
        with self._lock:
            self._entries[key] = (time.monotonic(), value)

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)
