"""Tests for the per-dataset lance scan circuit breaker (v1.10.4).

Covers three layers:
- ``LanceScanBreaker`` trip / cooldown / window / fail-open semantics (against a
  dict-backed fake Redis with TTL expiry).
- ``OlapSearchBridge._resolve_scan_mode`` — override, vector guard, breaker
  demotion, global fallback, auto-promote protection.
- The ``on_uninterruptible`` callback wiring — it must mark the session unhealthy
  AND record a breaker strike.
"""

from __future__ import annotations

import logging

import pyarrow as pa
from arrow_lake.config import OlapConfig
from arrow_lake.query.olap import OlapSearchBridge
from arrow_lake.query.scan_breaker import LanceScanBreaker


class FakeRedis:
    """Dict-backed Redis with TTL expiry driven by an injectable clock."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[object, float | None]] = {}
        self._t = 0.0

    def advance(self, seconds: float) -> None:
        self._t += seconds

    def _alive(self, key: str) -> bool:
        if key not in self._store:
            return False
        exp = self._store[key][1]
        if exp is not None and self._t >= exp:
            del self._store[key]
            return False
        return True

    def incr(self, key: str) -> int:
        if not self._alive(key):
            self._store[key] = (1, None)
            return 1
        val, exp = self._store[key]
        self._store[key] = (int(val) + 1, exp)
        return int(val) + 1

    def expire(self, key: str, ttl: int) -> bool:
        if not self._alive(key):
            return False
        val, _ = self._store[key]
        self._store[key] = (val, self._t + ttl)
        return True

    def exists(self, key: str) -> int:
        return 1 if self._alive(key) else 0

    def set(self, key: str, val: object, ex: int | None = None, nx: bool = False) -> bool:
        if nx and self._alive(key):
            return False  # NX fails: key already exists
        self._store[key] = (val, self._t + ex if ex else None)
        return True

    def get(self, key: str):
        return self._store[key][0] if self._alive(key) else None

    def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0


def _breaker(redis, **kw) -> LanceScanBreaker:
    return LanceScanBreaker(
        threshold=2, window_s=600, cooldown_s=1800, redis_client=redis, **kw,
    )


# ---------------------------------------------------------------------------
# LanceScanBreaker core semantics
# ---------------------------------------------------------------------------


def test_not_tripped_below_threshold():
    # Arrange
    fake = FakeRedis()
    b = _breaker(fake)

    # Act
    b.record_trip("ds")

    # Assert — one strike, below threshold
    assert b.is_tripped("ds") is False


def test_trips_at_threshold():
    # Arrange
    fake = FakeRedis()
    b = _breaker(fake)

    # Act
    b.record_trip("ds")
    b.record_trip("ds")

    # Assert
    assert b.is_tripped("ds") is True


def test_window_resets_trip_count():
    # Arrange — first strike, then the window expires before the second.
    fake = FakeRedis()
    b = _breaker(fake)

    # Act
    b.record_trip("ds")
    fake.advance(601)  # trips key TTL (window_s=600) elapsed
    b.record_trip("ds")  # fresh window → count restarts at 1

    # Assert
    assert b.is_tripped("ds") is False


def test_cooldown_expires_and_recovers():
    # Arrange — trip the breaker.
    fake = FakeRedis()
    b = _breaker(fake)
    b.record_trip("ds")
    b.record_trip("ds")
    assert b.is_tripped("ds") is True

    # Act — cooldown elapses
    fake.advance(1801)

    # Assert
    assert b.is_tripped("ds") is False


def test_cooldown_not_extended_by_repeated_trips():
    # Arrange — trip; cooldown set once (NX) at t=0, expires at 1800.
    fake = FakeRedis()
    b = _breaker(fake)
    b.record_trip("ds")
    b.record_trip("ds")
    assert b.is_tripped("ds") is True

    # Act — partway through cooldown, more strikes land (NX must NOT reset window)
    fake.advance(1700)
    assert b.is_tripped("ds") is True
    b.record_trip("ds")
    b.record_trip("ds")
    # advance past the ORIGINAL 1800s expiry (only 150s more → t=1850)
    fake.advance(150)

    # Assert — cooldown expired on schedule; it was not extended to 1700+1800
    assert b.is_tripped("ds") is False


def test_failopen_when_redis_absent():
    # Arrange — no redis_client, and lazy client returns None in this env.
    b = LanceScanBreaker(threshold=2, redis_client=None)

    # Act
    b.record_trip("ds")

    # Assert — fail-open: never trips, never raises
    assert b.is_tripped("ds") is False


def test_failopen_on_redis_errors():
    # Arrange — a client that raises on every op.
    class Boom:
        def incr(self, k):
            raise RuntimeError("down")

        def expire(self, k, t):
            raise RuntimeError("down")

        def exists(self, k):
            raise RuntimeError("down")

        def set(self, k, v, ex=None):
            raise RuntimeError("down")

    b = LanceScanBreaker(threshold=2, redis_client=Boom())

    # Act / Assert — must not raise; fail-open returns False
    b.record_trip("ds")
    assert b.is_tripped("ds") is False


def test_trip_logs_warning_at_threshold(caplog):
    # Arrange
    fake = FakeRedis()
    b = _breaker(fake)

    # Act
    with caplog.at_level(logging.WARNING, logger="arrow_lake.query.scan_breaker"):
        b.record_trip("ds")
        b.record_trip("ds")

    # Assert
    assert any("lance_cb_tripped" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# OlapSearchBridge._resolve_scan_mode
# ---------------------------------------------------------------------------

_PLAIN = pa.table({"a": [1, 2, 3]})
_VECTOR = pa.table({"v": pa.array([[1.0, 2.0]], type=pa.list_(pa.float32(), 2))})


def _bridge(overrides=None, auto_promote=False, breaker=None) -> OlapSearchBridge:
    cfg = OlapConfig(
        lance_scan_mode_overrides=overrides or {},
        lance_auto_promote=auto_promote,
    )
    b = OlapSearchBridge(storage=None, config=cfg)
    if breaker is not None:
        b._breaker = breaker
    return b


def test_resolve_override_to_native():
    # Arrange
    b = _bridge(overrides={"ds": "native"})

    # Act / Assert
    assert b._resolve_scan_mode("ds", _PLAIN) == "native"


def test_resolve_override_ignored_for_vector_dataset():
    # Arrange — native override on a vector dataset must demote to global default.
    b = _bridge(overrides={"ds": "native"})

    # Act / Assert
    assert b._resolve_scan_mode("ds", _VECTOR) == "pyarrow_fallback"


def test_resolve_override_demoted_when_tripped():
    # Arrange — trip the breaker for the overridden dataset.
    fake = FakeRedis()
    bk = _breaker(fake)
    bk.record_trip("ds")
    bk.record_trip("ds")
    b = _bridge(overrides={"ds": "native"}, breaker=bk)

    # Act / Assert — breaker cooldown overrides the opt-in
    assert b._resolve_scan_mode("ds", _PLAIN) == "pyarrow_fallback"


def test_resolve_no_override_uses_global_default():
    # Arrange / Act
    b = _bridge()

    # Assert
    assert b._resolve_scan_mode("ds", _PLAIN) == "pyarrow_fallback"


def test_resolve_auto_promote_demoted_when_tripped():
    # Arrange — auto_promote would normally flip a vector-less dataset to native
    # ("auto"), but the breaker must still demote it.
    fake = FakeRedis()
    bk = _breaker(fake)
    bk.record_trip("ds")
    bk.record_trip("ds")
    b = _bridge(auto_promote=True, breaker=bk)

    # Act / Assert
    assert b._resolve_scan_mode("ds", _PLAIN) == "pyarrow_fallback"


# ---------------------------------------------------------------------------
# on_uninterruptible callback wiring
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self) -> None:
        self.marked = False

    def mark_unhealthy(self) -> None:
        self.marked = True


def test_on_uninterruptible_marks_unhealthy_and_trips():
    # Arrange
    fake = FakeRedis()
    bk = _breaker(fake)
    b = _bridge(breaker=bk)
    sess = _FakeSession()
    cb = b._on_uninterruptible_tripper(sess, "ds")

    # Act — first strike: marks session, below threshold (not tripped yet)
    cb()
    assert sess.marked is True
    assert bk.is_tripped("ds") is False

    # Act — second strike: trips
    cb()
    assert bk.is_tripped("ds") is True


def test_on_uninterruptible_handles_session_without_mark():
    # Arrange — a plain object has no mark_unhealthy; must not raise.
    fake = FakeRedis()
    bk = _breaker(fake)
    b = _bridge(breaker=bk)
    cb = b._on_uninterruptible_tripper(object(), "ds")

    # Act / Assert
    cb()
    assert bk.is_tripped("ds") is False


# ---------------------------------------------------------------------------
# Multi-dataset support + per-dataset isolation (v1.10.4 Q2)
# ---------------------------------------------------------------------------


def test_breaker_state_is_per_dataset():
    # Arrange — trip dataset "a"; dataset "b" must be unaffected.
    fake = FakeRedis()
    b = _breaker(fake)

    # Act
    b.record_trip("a")
    b.record_trip("a")

    # Assert — "a" in cooldown, "b" open (independent Redis keys)
    assert b.is_tripped("a") is True
    assert b.is_tripped("b") is False


def test_resolve_multi_dataset_override_isolation():
    # Arrange — two native-opt-in datasets; trip only "a".
    fake = FakeRedis()
    bk = _breaker(fake)
    bk.record_trip("a")
    bk.record_trip("a")
    b = _bridge(overrides={"a": "native", "b": "native"}, breaker=bk)

    # Act / Assert — "a" demoted by its own breaker, "b" still native
    assert b._resolve_scan_mode("a", _PLAIN) == "pyarrow_fallback"
    assert b._resolve_scan_mode("b", _PLAIN) == "native"

