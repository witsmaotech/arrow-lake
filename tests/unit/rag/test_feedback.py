"""Tests for feedback loop in SessionStore."""

from __future__ import annotations

from arrow_lake.rag.session import SessionStore


class TestSaveFeedback:
    def test_save_and_get_feedback(self) -> None:
        store = SessionStore()
        store.save_feedback("s1", 1, "positive", comment="good")
        fb = store.get_feedback("s1")
        assert len(fb) == 1
        assert fb[0]["rating"] == "positive"
        assert fb[0]["comment"] == "good"

    def test_feedback_includes_timestamp(self) -> None:
        store = SessionStore()
        store.save_feedback("s1", 1, "negative")
        fb = store.get_feedback("s1")
        assert fb[0]["timestamp"] > 0

    def test_multiple_feedbacks(self) -> None:
        store = SessionStore()
        store.save_feedback("s1", 1, "positive")
        store.save_feedback("s1", 2, "negative")
        fb = store.get_feedback("s1")
        assert len(fb) == 2

    def test_get_feedback_wrong_session(self) -> None:
        store = SessionStore()
        store.save_feedback("s1", 1, "positive")
        assert store.get_feedback("s2") == []

    def test_flagged_citations_stored(self) -> None:
        store = SessionStore()
        store.save_feedback("s1", 1, "negative", flagged_citation_indices=(0, 2))
        fb = store.get_feedback("s1")
        assert fb[0]["flagged_citation_indices"] == [0, 2]
