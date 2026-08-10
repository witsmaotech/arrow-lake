"""Integration test: LanceScanBreaker against a real Redis (gated).

Unit tests use FakeRedis; this verifies the breaker's real redis-py command
surface (INCR/EXPIRE/SET NX/EXISTS) catches wiring mistakes the fake can't.
Skipped when Redis is unavailable (no ARROW_LAKE__REDIS__URL / REDIS_URL, or
unreachable) so it never fails in Redis-less CI.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

redis = pytest.importorskip("redis")  # skip the whole module if redis-py absent

from arrow_lake.query.scan_breaker import LanceScanBreaker  # noqa: E402 — after importorskip

_URL = os.environ.get("ARROW_LAKE__REDIS__URL") or os.environ.get("REDIS_URL", "")


def _connect():
    if not _URL:
        return None
    try:
        c = redis.Redis.from_url(_URL, socket_connect_timeout=2, socket_timeout=2)
        c.ping()
        return c
    except Exception:
        return None


@pytest.fixture(scope="module")
def real_client():
    c = _connect()
    if c is None:
        pytest.skip("Redis unavailable — set ARROW_LAKE__REDIS__URL/REDIS_URL")
    yield c


def _ds() -> str:
    return f"it_{uuid.uuid4().hex[:8]}"


def _cleanup(c, *datasets: str) -> None:
    for ds in datasets:
        c.delete(f"arrow-lake:lance_cb:{ds}:trips", f"arrow-lake:lance_cb:{ds}:cooldown")


def test_record_trip_roundtrip_real(real_client):
    # Arrange
    ds = _ds()
    b = LanceScanBreaker(threshold=2, redis_client=real_client)
    try:
        # Act / Assert — below threshold: open; at threshold: tripped
        b.record_trip(ds)
        assert b.is_tripped(ds) is False
        b.record_trip(ds)
        assert b.is_tripped(ds) is True
    finally:
        _cleanup(real_client, ds)


def test_per_dataset_isolation_real(real_client):
    # Arrange — two datasets on one shared Redis; trip only the first.
    a, other = _ds(), _ds()
    b = LanceScanBreaker(threshold=2, redis_client=real_client)
    try:
        # Act
        b.record_trip(a)
        b.record_trip(a)
        # Assert — independent Redis keys; tripping "a" never trips "other"
        assert b.is_tripped(a) is True
        assert b.is_tripped(other) is False
    finally:
        _cleanup(real_client, a, other)


def test_cooldown_nx_not_extended_real(real_client):
    # Arrange — short cooldown so the NX signal is testable in seconds.
    ds = _ds()
    key = f"arrow-lake:lance_cb:{ds}:cooldown"
    b = LanceScanBreaker(threshold=2, cooldown_s=10, redis_client=real_client)
    try:
        b.record_trip(ds)
        b.record_trip(ds)  # cooldown set once (NX), TTL 10s
        assert b.is_tripped(ds) is True
        # Act — let part of the cooldown elapse, then strike again.
        time.sleep(4)  # TTL now ~6s
        b.record_trip(ds)
        b.record_trip(ds)  # WITHOUT NX this would reset the TTL back to 10s
        after = real_client.pttl(key) / 1000
        # Assert — TTL kept declining (~5-6s); a reset would be ~10s.
        assert after < 8, f"cooldown TTL reset to {after}s — NX not honored"
    finally:
        _cleanup(real_client, ds)
