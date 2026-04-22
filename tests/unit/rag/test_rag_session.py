"""Tests for RAG session history — M2 Day 8-9."""

from __future__ import annotations

import pytest
from arrow_lake.rag.pipeline import RAGCitation, RAGResponse
from arrow_lake.rag.session import SessionStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    answer: str = "Test answer",
    citations: list[RAGCitation] | None = None,
) -> RAGResponse:
    return RAGResponse(
        answer=answer,
        citations=tuple(citations or [RAGCitation(0, "ds", "r", 0.9, "excerpt")]),
        retrieval_count=2,
        context_tokens=50,
        llm_usage={"total_tokens": 15},
        latency_ms=100.0,
    )


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------


class TestSessionStore:
    @pytest.fixture()
    def store(self) -> SessionStore:
        return SessionStore(history_dataset="_rag_sessions")

    def test_save_turn(self, store: SessionStore) -> None:
        resp = _make_response()
        store.save_turn(
            session_id="sess-1",
            question="What is AI?",
            response=resp,
        )
        history = store.get_history("sess-1")
        assert len(history) == 1
        assert history[0]["question"] == "What is AI?"
        assert history[0]["answer"] == "Test answer"
        assert history[0]["session_id"] == "sess-1"

    def test_save_multiple_turns(self, store: SessionStore) -> None:
        store.save_turn("sess-1", "Q1", _make_response("A1"))
        store.save_turn("sess-1", "Q2", _make_response("A2"))
        store.save_turn("sess-1", "Q3", _make_response("A3"))

        history = store.get_history("sess-1")
        assert len(history) == 3
        assert history[0]["question"] == "Q1"
        assert history[1]["question"] == "Q2"
        assert history[2]["question"] == "Q3"

    def test_get_history_nonexistent(self, store: SessionStore) -> None:
        history = store.get_history("does-not-exist")
        assert history == []

    def test_get_history_sorted_by_timestamp(self, store: SessionStore) -> None:
        store.save_turn("sess-1", "Q1", _make_response("A1"))
        store.save_turn("sess-1", "Q2", _make_response("A2"))

        history = store.get_history("sess-1")
        assert history[0]["turn_id"] < history[1]["turn_id"]

    def test_delete_session(self, store: SessionStore) -> None:
        store.save_turn("sess-1", "Q", _make_response("A"))
        store.save_turn("sess-2", "Q", _make_response("A"))

        store.delete_session("sess-1")
        assert store.get_history("sess-1") == []
        assert len(store.get_history("sess-2")) == 1

    def test_delete_nonexistent_is_noop(self, store: SessionStore) -> None:
        store.delete_session("does-not-exist")  # should not raise

    def test_turn_has_metadata(self, store: SessionStore) -> None:
        resp = _make_response("A", citations=[
            RAGCitation(0, "docs", "r1", 0.9, "text"),
        ])
        store.save_turn("sess-1", "Q", resp)

        history = store.get_history("sess-1")
        turn = history[0]
        assert "model" in turn
        assert "dataset_name" in turn
        assert "citations" in turn
        assert "latency_ms" in turn
        assert turn["session_id"] == "sess-1"  # passed via save_turn, not mutation

    def test_list_sessions(self, store: SessionStore) -> None:
        store.save_turn("sess-a", "Q1", _make_response("A1"))
        store.save_turn("sess-b", "Q2", _make_response("A2"))
        store.save_turn("sess-a", "Q3", _make_response("A3"))

        sessions = store.list_sessions()
        assert len(sessions) == 2
        session_ids = {s["session_id"] for s in sessions}
        assert "sess-a" in session_ids
        assert "sess-b" in session_ids

    def test_list_sessions_empty(self, store: SessionStore) -> None:
        assert store.list_sessions() == []
