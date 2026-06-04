"""Comprehensive tests for RAG session.py — targeting uncovered paths.

Covers:
- Max turns per session eviction
- Max sessions global eviction
- TTL-based expiration (_evict_expired, cleanup_expired)
- save_feedback and get_feedback
- list_sessions ordering
- Edge cases: response without attributes, zero TTL
"""

from __future__ import annotations

import time

import pytest

from arrow_lake.rag.pipeline import RAGCitation, RAGResponse
from arrow_lake.rag.session import SessionStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    answer: str = "Test answer",
    latency_ms: float = 100.0,
    llm_usage: dict | None = None,
) -> RAGResponse:
    return RAGResponse(
        answer=answer,
        citations=(RAGCitation(0, "ds", "r", 0.9, "excerpt"),),
        retrieval_count=2,
        context_tokens=50,
        llm_usage=llm_usage or {"total_tokens": 15},
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# Max turns per session eviction
# ---------------------------------------------------------------------------


class TestMaxTurnsPerSession:
    """Test per-session turn limit enforcement."""

    def test_evicts_oldest_turns_on_limit(self) -> None:
        store = SessionStore(max_turns_per_session=3)
        store.save_turn("s1", "Q1", _make_response("A1"))
        store.save_turn("s1", "Q2", _make_response("A2"))
        store.save_turn("s1", "Q3", _make_response("A3"))

        # 4th turn should trigger eviction of session s1
        store.save_turn("s1", "Q4", _make_response("A4"))

        history = store.get_history("s1")
        # After eviction, turn_counter resets and only Q4 remains
        assert len(history) == 1
        assert history[0]["question"] == "Q4"

    def test_eviction_does_not_affect_other_sessions(self) -> None:
        store = SessionStore(max_turns_per_session=2)
        store.save_turn("s1", "Q1", _make_response("A1"))
        store.save_turn("s2", "Q1", _make_response("A1"))

        # s1 reaches limit
        store.save_turn("s1", "Q2", _make_response("A2"))
        store.save_turn("s1", "Q3", _make_response("A3"))  # triggers eviction

        # s2 should be unaffected
        assert len(store.get_history("s2")) == 1


# ---------------------------------------------------------------------------
# Max sessions global eviction
# ---------------------------------------------------------------------------


class TestMaxSessionsEviction:
    """Test global session count limit."""

    def test_evicts_oldest_session_on_limit(self) -> None:
        store = SessionStore(max_sessions=2, session_ttl_seconds=0)
        store.save_turn("s1", "Q1", _make_response("A1"))
        store.save_turn("s2", "Q2", _make_response("A2"))

        # Third session triggers eviction of oldest
        store.save_turn("s3", "Q3", _make_response("A3"))

        sessions = store.list_sessions()
        session_ids = {s["session_id"] for s in sessions}
        # One of s1/s2 should be evicted, s3 should exist
        assert "s3" in session_ids
        assert len(session_ids) <= 2


# ---------------------------------------------------------------------------
# TTL-based expiration
# ---------------------------------------------------------------------------


class TestTTLExpiration:
    """Test TTL-based turn expiration."""

    def test_evict_expired_removes_old_turns(self) -> None:
        store = SessionStore(session_ttl_seconds=1)
        store.save_turn("s1", "Q1", _make_response("A1"))

        # Manually set timestamp to past
        store._session_index["s1"][0]["timestamp"] = time.time() - 10

        # Trigger eviction
        store._evict_expired("s1")

        # Old turn should be gone
        assert len(store.get_history("s1")) == 0

    def test_evict_expired_keeps_recent_turns(self) -> None:
        store = SessionStore(session_ttl_seconds=3600)
        store.save_turn("s1", "Q1", _make_response("A1"))
        store._evict_expired("s1")
        assert len(store.get_history("s1")) == 1

    def test_evict_expired_zero_ttl_is_noop(self) -> None:
        store = SessionStore(session_ttl_seconds=0)
        store.save_turn("s1", "Q1", _make_response("A1"))
        store._evict_expired("s1")
        assert len(store.get_history("s1")) == 1

    def test_evict_expired_nonexistent_session(self) -> None:
        store = SessionStore(session_ttl_seconds=60)
        store._evict_expired("nonexistent")  # should not raise

    def test_evict_expired_removes_all_turns_clears_session(self) -> None:
        store = SessionStore(session_ttl_seconds=1)
        store.save_turn("s1", "Q1", _make_response("A1"))

        # Make turn expired
        store._session_index["s1"][0]["timestamp"] = time.time() - 10
        store._evict_expired("s1")

        # Session should be fully removed from index and counter
        assert "s1" not in store._session_index
        assert "s1" not in store._turn_counter

    def test_evict_expired_partial_eviction(self) -> None:
        store = SessionStore(session_ttl_seconds=5)
        store.save_turn("s1", "Q1", _make_response("A1"))

        # Add a second turn
        store.save_turn("s1", "Q2", _make_response("A2"))

        # Make only the first turn expired
        store._session_index["s1"][0]["timestamp"] = time.time() - 10
        # Keep second turn recent
        store._session_index["s1"][1]["timestamp"] = time.time()

        store._evict_expired("s1")

        history = store.get_history("s1")
        assert len(history) == 1
        assert history[0]["question"] == "Q2"


# ---------------------------------------------------------------------------
# cleanup_expired
# ---------------------------------------------------------------------------


class TestCleanupExpired:
    """Test global expired turn cleanup."""

    def test_cleanup_expired_removes_old_turns(self) -> None:
        store = SessionStore(session_ttl_seconds=1)
        store.save_turn("s1", "Q1", _make_response("A1"))
        store.save_turn("s2", "Q2", _make_response("A2"))

        # Expire s1 turns
        for turn in store._session_index["s1"]:
            turn["timestamp"] = time.time() - 10

        evicted = store.cleanup_expired()
        assert evicted == 1
        assert len(store.get_history("s1")) == 0
        assert len(store.get_history("s2")) == 1

    def test_cleanup_expired_zero_ttl_returns_zero(self) -> None:
        store = SessionStore(session_ttl_seconds=0)
        store.save_turn("s1", "Q1", _make_response("A1"))
        assert store.cleanup_expired() == 0

    def test_cleanup_nothing_expired(self) -> None:
        store = SessionStore(session_ttl_seconds=3600)
        store.save_turn("s1", "Q1", _make_response("A1"))
        assert store.cleanup_expired() == 0


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


class TestFeedback:
    """Test feedback save and retrieval."""

    def test_save_and_get_feedback(self) -> None:
        store = SessionStore()
        store.save_feedback(
            session_id="s1",
            turn_id=1,
            rating="positive",
            comment="Great answer",
        )
        feedback = store.get_feedback("s1")
        assert len(feedback) == 1
        assert feedback[0]["rating"] == "positive"
        assert feedback[0]["comment"] == "Great answer"
        assert feedback[0]["turn_id"] == 1

    def test_feedback_with_flagged_citations(self) -> None:
        store = SessionStore()
        store.save_feedback(
            session_id="s1",
            turn_id=1,
            rating="negative",
            flagged_citation_indices=(0, 2),
            comment="Wrong citations",
        )
        feedback = store.get_feedback("s1")
        assert feedback[0]["flagged_citation_indices"] == [0, 2]

    def test_get_feedback_empty(self) -> None:
        store = SessionStore()
        assert store.get_feedback("nonexistent") == []

    def test_feedback_across_sessions(self) -> None:
        store = SessionStore()
        store.save_feedback("s1", 1, "positive")
        store.save_feedback("s2", 1, "negative")
        assert len(store.get_feedback("s1")) == 1
        assert len(store.get_feedback("s2")) == 1
        assert store.get_feedback("s1")[0]["rating"] == "positive"


# ---------------------------------------------------------------------------
# list_sessions ordering
# ---------------------------------------------------------------------------


class TestListSessionsOrdering:
    """Test list_sessions returns sorted by timestamp descending."""

    def test_sorted_by_timestamp_descending(self) -> None:
        store = SessionStore(session_ttl_seconds=0)
        store.save_turn("s1", "Q1", _make_response("A1"))
        store.save_turn("s2", "Q2", _make_response("A2"))
        store.save_turn("s3", "Q3", _make_response("A3"))

        sessions = store.list_sessions()
        # Most recent session first
        timestamps = [s["timestamp"] for s in sessions]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_shows_latest_turn_info(self) -> None:
        store = SessionStore(session_ttl_seconds=0)
        store.save_turn("s1", "Q1", _make_response("A1"))
        store.save_turn("s1", "Q2", _make_response("A2"))

        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["last_question"] == "Q2"
        assert sessions[0]["turn_id"] == 2


# ---------------------------------------------------------------------------
# Response without optional attributes
# ---------------------------------------------------------------------------


class TestResponseWithoutAttributes:
    """Test save_turn with plain objects missing optional attrs."""

    def test_response_without_citations(self) -> None:
        store = SessionStore()

        class PlainResponse:
            answer = "plain answer"
            citations = []
            latency_ms = None
            llm_usage = None

        store.save_turn("s1", "Q", PlainResponse())
        history = store.get_history("s1")
        assert len(history) == 1
        assert history[0]["answer"] == "plain answer"
        assert history[0]["citations"] == []

    def test_response_without_latency(self) -> None:
        store = SessionStore()

        class MinimalResponse:
            answer = "min"
            citations = []
            latency_ms = None
            llm_usage = None

        store.save_turn("s1", "Q", MinimalResponse())
        history = store.get_history("s1")
        assert history[0]["latency_ms"] is None

    def test_response_without_llm_usage(self) -> None:
        store = SessionStore()

        class NoUsageResponse:
            answer = "no usage"
            citations = []
            latency_ms = 50.0
            llm_usage = None

        store.save_turn("s1", "Q", NoUsageResponse())
        history = store.get_history("s1")
        assert history[0]["llm_usage"] is None


# ---------------------------------------------------------------------------
# Custom init parameters
# ---------------------------------------------------------------------------


class TestSessionStoreInit:
    """Test SessionStore initialization with custom parameters."""

    def test_custom_dataset_name(self) -> None:
        store = SessionStore(history_dataset="custom_sessions")
        assert store._history_dataset == "custom_sessions"

    def test_default_parameters(self) -> None:
        store = SessionStore()
        assert store._max_sessions == 10000
        assert store._max_turns_per_session == 100
        assert store._session_ttl_seconds == 86400

    def test_zero_ttl(self) -> None:
        store = SessionStore(session_ttl_seconds=0)
        assert store._session_ttl_seconds == 0
