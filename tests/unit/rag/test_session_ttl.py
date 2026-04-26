"""Tests for session TTL eviction in SessionStore."""

from __future__ import annotations

import time

from arrow_lake.rag.session import SessionStore


class TestSessionTTL:
    def test_evict_expired_turns_on_save(self) -> None:
        store = SessionStore(session_ttl_seconds=1)
        store.save_turn("s1", "q1", _fake_response())
        time.sleep(1.5)
        store.save_turn("s1", "q2", _fake_response())
        history = store.get_history("s1")
        assert len(history) == 1
        assert history[0]["question"] == "q2"

    def test_no_eviction_when_ttl_zero(self) -> None:
        store = SessionStore(session_ttl_seconds=0)
        store.save_turn("s1", "q1", _fake_response())
        store.save_turn("s1", "q2", _fake_response())
        history = store.get_history("s1")
        assert len(history) == 2

    def test_cleanup_expired_sweeps_all(self) -> None:
        store = SessionStore(session_ttl_seconds=1)
        store.save_turn("s1", "q1", _fake_response())
        store.save_turn("s2", "q1", _fake_response())
        time.sleep(1.5)
        evicted = store.cleanup_expired()
        assert evicted == 2

    def test_cleanup_expired_preserves_recent(self) -> None:
        store = SessionStore(session_ttl_seconds=86400)
        store.save_turn("s1", "q1", _fake_response())
        evicted = store.cleanup_expired()
        assert evicted == 0
        assert len(store.get_history("s1")) == 1


def _fake_response():
    return type("R", (), {"answer": "a", "citations": [], "latency_ms": 0})()
