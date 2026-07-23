"""Tests for RedisRateLimiter — multi-worker lockout + fail-open semantics.

v1.9.2 批5. Uses a self-contained in-memory fake redis client (no fakeredis
dependency) that two ``RedisRateLimiter`` instances share, simulating two
uvicorn workers against one Redis. Validates the core multi-worker bug:
single-process in-memory counters let each worker independently under-count
failures; a shared Redis bucket must aggregate them.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# In-memory fake redis (subset: incr/expire(nx)/ttl/pipeline/zset ops)
# ---------------------------------------------------------------------------


class _FakePipeline:
    def __init__(self, store: "_FakeRedis") -> None:
        self._store = store
        self._cmds: list = []

    def incr(self, key):
        self._cmds.append(("incr", key))

    def expire(self, key, secs, nx=False):
        self._cmds.append(("expire", key, secs, nx))

    def zremrangebyscore(self, key, lo, hi):
        self._cmds.append(("zremrangebyscore", key, lo, hi))

    def zcard(self, key):
        self._cmds.append(("zcard", key))

    def zadd(self, key, mapping):
        self._cmds.append(("zadd", key, mapping))

    def execute(self):
        if not self._store._alive:
            raise ConnectionError("redis dead")
        results = []
        for c in self._cmds:
            op = c[0]
            if op == "incr":
                key = c[1]
                self._store._strs[key] = self._store._strs.get(key, 0) + 1
                results.append(self._store._strs[key])
            elif op == "expire":
                _, key, secs, nx = c
                if nx and key in self._store._ttls:
                    results.append(0)
                else:
                    self._store._ttls[key] = time.time() + secs
                    results.append(1)
            elif op == "zremrangebyscore":
                _, key, lo, hi = c
                zset = self._store._zsets.setdefault(key, {})
                for m in [m for m, s in zset.items() if s < hi]:
                    del zset[m]
                results.append(0)
            elif op == "zcard":
                key = c[1]
                results.append(len(self._store._zsets.get(key, {})))
            elif op == "zadd":
                _, key, mapping = c
                zset = self._store._zsets.setdefault(key, {})
                for m, s in mapping.items():
                    zset[m] = s
                results.append(len(mapping))
        self._cmds = []
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self._strs: dict[str, int] = {}
        self._ttls: dict[str, float] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self._alive = True

    def ping(self):
        if not self._alive:
            raise ConnectionError("dead")
        return True

    def pipeline(self):
        return _FakePipeline(self)

    def incr(self, key):
        self._strs[key] = self._strs.get(key, 0) + 1
        return self._strs[key]

    def expire(self, key, secs, nx=False):
        if nx and key in self._ttls:
            return 0
        self._ttls[key] = time.time() + secs
        return 1

    def ttl(self, key):
        if key not in self._ttls:
            return -1
        rem = self._ttls[key] - time.time()
        return max(1, int(rem))

    def zrangebyscore(self, key, lo, hi):
        return [m for m, s in self._zsets.get(key, {}).items() if lo <= s <= hi]

    def zrange(self, key, lo, hi):
        members = list(self._zsets.get(key, {}).keys())
        return members[lo : hi + 1] if hi != -1 else members[lo:]

    def zrem(self, key, *members):
        zset = self._zsets.get(key, {})
        n = 0
        for m in members:
            if m in zset:
                del zset[m]
                n += 1
        return n

    def close(self):
        self._alive = False


def _make_limiter(shared_store: _FakeRedis, *, connected: bool = True):
    """Build a RedisRateLimiter wired to the shared fake store."""
    from arrow_lake.api import _redis_rate_limit as mod

    limiter = mod.RedisRateLimiter.__new__(mod.RedisRateLimiter)
    limiter._prefix = "arrow_lake:rl:"
    limiter._login_bucket = "arrow_lake:rl:login"
    limiter._login_fail_limit = 10
    limiter._login_lockout_seconds = 900
    limiter._redis = shared_store if connected else None
    limiter._connected = connected
    return limiter


# ---------------------------------------------------------------------------
# (ip, path) request counter
# ---------------------------------------------------------------------------


class TestHitCounter:
    def test_under_limit_allows_and_decrements_remaining(self):
        store = _FakeRedis()
        a = _make_limiter(store)
        allowed, remaining, retry = a.hit("1.2.3.4", "/api/x", limit=5, window=60)
        assert allowed is True
        assert remaining == 4
        assert retry == 0

    def test_over_limit_blocks_with_retry_after(self):
        store = _FakeRedis()
        a = _make_limiter(store)
        for _ in range(5):
            a.hit("1.2.3.4", "/api/x", limit=5, window=60)
        allowed, remaining, retry = a.hit("1.2.3.4", "/api/x", limit=5, window=60)
        assert allowed is False
        assert remaining == 0
        assert retry >= 1

    def test_two_workers_share_counter(self):
        """Two instances against one Redis: the 6th request is blocked even
        if it lands on worker B while A served the first 5."""
        store = _FakeRedis()
        a = _make_limiter(store)
        b = _make_limiter(store)
        for _ in range(5):
            assert a.hit("9.9.9.9", "/p", limit=5, window=60)[0] is True
        # worker B sees the aggregate count → blocked
        allowed, _, _ = b.hit("9.9.9.9", "/p", limit=5, window=60)
        assert allowed is False


# ---------------------------------------------------------------------------
# (username, ip) login lockout
# ---------------------------------------------------------------------------


class TestLoginLockout:
    def test_under_limit_not_locked(self):
        store = _FakeRedis()
        a = _make_limiter(store)
        for _ in range(9):
            a.record_login_failure("alice", "1.1.1.1")
        locked, n = a.check_login("alice", "1.1.1.1")
        assert locked is False
        assert n == 9

    def test_at_limit_locked(self):
        store = _FakeRedis()
        a = _make_limiter(store)
        for _ in range(10):
            a.record_login_failure("alice", "1.1.1.1")
        locked, n = a.check_login("alice", "1.1.1.1")
        assert locked is True
        assert n == 10

    def test_multi_worker_aggregate_lockout(self):
        """The core multi-worker bug: worker A fails 5, worker B fails 5 →
        11th attempt locked on BOTH workers (in-memory would show only 5 each)."""
        store = _FakeRedis()
        a = _make_limiter(store)
        b = _make_limiter(store)
        for _ in range(5):
            a.record_login_failure("bob", "2.2.2.2")
        for _ in range(5):
            b.record_login_failure("bob", "2.2.2.2")
        locked_a, _ = a.check_login("bob", "2.2.2.2")
        locked_b, _ = b.check_login("bob", "2.2.2.2")
        assert locked_a is True
        assert locked_b is True

    def test_per_pair_isolation(self):
        store = _FakeRedis()
        a = _make_limiter(store)
        for _ in range(10):
            a.record_login_failure("alice", "1.1.1.1")
        # alice from a different IP is not locked
        locked, _ = a.check_login("alice", "3.3.3.3")
        assert locked is False
        # different user same IP not locked
        locked, _ = a.check_login("carol", "1.1.1.1")
        assert locked is False

    def test_reset_on_success(self):
        store = _FakeRedis()
        a = _make_limiter(store)
        for _ in range(8):
            a.record_login_failure("alice", "1.1.1.1")
        a.reset_login("alice", "1.1.1.1")
        locked, n = a.check_login("alice", "1.1.1.1")
        assert locked is False
        assert n == 0


# ---------------------------------------------------------------------------
# Fail-open: Redis unavailable / hiccup never raises
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_disconnected_returns_none(self):
        store = _FakeRedis()
        a = _make_limiter(store, connected=False)
        # all methods return None / no-op when disconnected
        assert a.hit("x", "/p", limit=1, window=1) is None
        assert a.check_login("u", "i") is None
        a.record_login_failure("u", "i")  # must not raise
        a.reset_login("u", "i")  # must not raise

    def test_hit_hiccup_marks_disconnected_and_returns_none(self):
        store = _FakeRedis()
        a = _make_limiter(store)
        store._alive = False  # simulate Redis death mid-flight
        # hit must NOT raise — fail-open returns None
        res = a.hit("x", "/p", limit=1, window=1)
        assert res is None
        assert a.is_connected is False

    def test_create_rate_limiter_disabled_returns_none(self):
        from arrow_lake.api._redis_rate_limit import create_rate_limiter

        cfg = MagicMock(enabled=False)
        assert create_rate_limiter(cfg) is None

    def test_limiter_with_no_redis_package(self):
        """If redis module import fails, limiter stays disconnected (fail-open)."""
        from arrow_lake.api import _redis_rate_limit as mod

        # Force ImportError path
        original = mod._redis_module
        mod._redis_module = None
        try:
            lim = mod.RedisRateLimiter(redis_url="redis://x:1/0")
            assert lim.is_connected is False
            assert lim.hit("x", "/p", limit=1, window=1) is None
        finally:
            mod._redis_module = original
