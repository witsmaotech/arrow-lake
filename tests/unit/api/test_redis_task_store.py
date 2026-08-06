"""Tests for RedisTaskStore lazy reconnection (tasks-flicker fix).

Regression: ``_handle_error`` set ``_connected=False`` and never reconnected, so
a single transient Redis error permanently blinded a worker to cross-worker
task state until gunicorn recycled that worker — polls load-balanced to other
workers returned a different task set, so tasks blinked in and out. With
``_ensure_connected``, the next call re-pings and recovers automatically.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from arrow_lake.api._redis_task_store import RedisTaskStore


def _make_store() -> RedisTaskStore:
    """Build a RedisTaskStore wired to a fake redis (bypass real connect)."""
    store = RedisTaskStore.__new__(RedisTaskStore)
    store._prefix = "t:task:"
    store._ttl = 7200
    store._index_key = "t:task:index"
    store._redis = MagicMock()
    store._connected = True
    return store


class TestLazyReconnect:
    def test_stays_connected_when_healthy(self) -> None:
        store = _make_store()
        assert store._ensure_connected() is True
        assert store._connected is True

    def test_reconnects_after_transient_outage(self) -> None:
        store = _make_store()
        # An operation fails → _handle_error marks the store disconnected.
        pipe = MagicMock()
        store._redis.pipeline.return_value = pipe
        pipe.execute.side_effect = Exception("connection lost")
        assert store.create_task({"task_id": "t1", "operation": "ingest"}) is False
        assert store._connected is False
        # Redis recovers (MagicMock ping succeeds by default) → reconnects.
        assert store._ensure_connected() is True
        assert store._connected is True

    def test_create_task_recovers_after_outage(self) -> None:
        """The core fix: after a failed write, the next write reconnects."""
        store = _make_store()
        pipe = MagicMock()
        store._redis.pipeline.return_value = pipe
        pipe.execute.side_effect = Exception("down")
        assert store.create_task({"task_id": "a"}) is False
        assert store._connected is False
        # Pipeline works again → _ensure_connected reconnects → write succeeds.
        pipe.execute.side_effect = None
        assert store.create_task({"task_id": "b", "operation": "ingest"}) is True
        assert store._connected is True

    def test_stays_down_while_unreachable(self) -> None:
        store = _make_store()
        store._redis.ping.side_effect = Exception("unreachable")
        store._connected = False
        assert store._ensure_connected() is False
        # Graceful fallbacks, never raise.
        assert store.create_task({"task_id": "x"}) is False
        assert store.get_task("x") is None
        assert store.list_task_ids() == []

    def test_read_methods_self_heal_after_outage(self) -> None:
        """get_task/list_task_ids recover via lazy reconnect (the flicker fix)."""
        store = _make_store()
        store._connected = False  # post-error state on this worker
        store._redis.hgetall.return_value = {}  # no data, but must not crash
        store._redis.smembers.return_value = set()
        # First read reconnects (ping ok) then returns the empty fallback.
        assert store.get_task("any") is None
        assert store._connected is True
        assert store.list_task_ids() == []
