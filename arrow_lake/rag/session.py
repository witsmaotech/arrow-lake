"""RAG session history management."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from typing import Any

logger = logging.getLogger(__name__)


class SessionStore:
    """In-memory store for RAG conversation sessions.

    Each turn is a dict with: session_id, turn_id, question, answer,
    dataset_name, model, citations, latency_ms, timestamp.

    Designed to be swappable with a Lance-backed implementation later.
    """

    def __init__(
        self,
        history_dataset: str = "_rag_sessions",
        max_sessions: int = 10000,
        max_turns_per_session: int = 100,
        session_ttl_seconds: int = 86400,
        *,
        session_store: Any = None,
    ) -> None:
        # v1.9.0: when a libSQL RagSessionStore is supplied it is the durable
        # source of truth (the vestigial history_dataset param is superseded).
        self._store = session_store
        self._history_dataset = history_dataset
        self._max_sessions = max_sessions
        self._max_turns_per_session = max_turns_per_session
        self._session_ttl_seconds = session_ttl_seconds
        self._turns: list[dict] = []
        self._turn_counter: dict[str, int] = {}
        self._session_index: dict[str, list[dict]] = {}
        self._feedback: list[dict] = []

    def save_turn(
        self,
        session_id: str,
        question: str,
        response: object,
    ) -> None:
        """Save a conversation turn."""
        if self._store is not None:
            self._store.save_turn(session_id, question, response)
            return
        # Enforce per-session turn limit
        if self._turn_counter.get(session_id, 0) >= self._max_turns_per_session:
            logger.warning(
                "Session %s reached max turns (%d), evicting oldest",
                session_id,
                self._max_turns_per_session,
            )
            self._turns = [t for t in self._turns if t["session_id"] != session_id]
            self._session_index.pop(session_id, None)
            self._turn_counter[session_id] = 0

        turn_num = self._turn_counter.get(session_id, 0) + 1
        self._turn_counter[session_id] = turn_num

        # Extract metadata from response
        answer = getattr(response, "answer", "")
        citations = getattr(response, "citations", [])
        latency_ms = getattr(response, "latency_ms", None)
        llm_usage = getattr(response, "llm_usage", None)

        turn: dict = {
            "session_id": session_id,
            "turn_id": turn_num,
            "question": question,
            "answer": answer,
            "model": "",
            "dataset_name": "",
            "citations": [asdict(c) for c in citations] if citations else [],
            "latency_ms": latency_ms,
            "llm_usage": llm_usage,
            "timestamp": time.time(),
        }
        self._turns.append(turn)
        self._session_index.setdefault(session_id, []).append(turn)

        # Enforce global session limit — evict oldest sessions
        if len(self._turn_counter) > self._max_sessions:
            while len(self._turn_counter) > self._max_sessions:
                # Find session with oldest turn using index (O(n) not O(n*m))
                oldest_sid = min(
                    self._turn_counter,
                    key=lambda sid: self._session_index[sid][0]["timestamp"]
                    if self._session_index.get(sid) else float("inf"),
                )
                self._turns = [t for t in self._turns if t["session_id"] != oldest_sid]
                self._session_index.pop(oldest_sid, None)
                self._turn_counter.pop(oldest_sid, None)
                logger.warning("Evicted session %s (max_sessions=%d)", oldest_sid, self._max_sessions)

        # Evict turns older than TTL
        if self._session_ttl_seconds > 0:
            self._evict_expired(session_id)

    def get_history(self, session_id: str) -> list[dict]:
        """Get conversation history for a session, sorted by turn_id."""
        if self._store is not None:
            return self._store.get_history(session_id)
        return sorted(
            self._session_index.get(session_id, []),
            key=lambda t: t["turn_id"],
        )

    def delete_session(self, session_id: str) -> None:
        """Delete all turns for a session."""
        if self._store is not None:
            self._store.delete_session(session_id)
            return
        self._turns = [t for t in self._turns if t["session_id"] != session_id]
        self._turn_counter.pop(session_id, None)
        self._session_index.pop(session_id, None)

    def _evict_expired(self, session_id: str) -> None:
        """Remove turns older than TTL from a session."""
        if self._session_ttl_seconds <= 0:
            return
        cutoff = time.time() - self._session_ttl_seconds
        turns = self._session_index.get(session_id, [])
        if not turns:
            return
        expired = [t for t in turns if t["timestamp"] < cutoff]
        if not expired:
            return
        self._turns = [t for t in self._turns if not (
            t["session_id"] == session_id and t["timestamp"] < cutoff
        )]
        self._session_index[session_id] = [t for t in turns if t["timestamp"] >= cutoff]
        remaining = len(self._session_index[session_id])
        self._turn_counter[session_id] = remaining
        if remaining == 0:
            self._session_index.pop(session_id, None)
            self._turn_counter.pop(session_id, None)
        logger.debug(
            "Evicted %d expired turns from session %s", len(expired), session_id,
        )

    def cleanup_expired(self) -> int:
        """Sweep all sessions and remove expired turns. Returns count evicted."""
        if self._store is not None:
            return self._store.cleanup_expired(self._session_ttl_seconds)
        if self._session_ttl_seconds <= 0:
            return 0
        cutoff = time.time() - self._session_ttl_seconds
        before = len(self._turns)
        self._turns = [t for t in self._turns if t["timestamp"] >= cutoff]
        evicted = before - len(self._turns)
        if evicted > 0:
            for sid in list(self._session_index.keys()):
                self._session_index[sid] = [
                    t for t in self._session_index[sid] if t["timestamp"] >= cutoff
                ]
                if not self._session_index[sid]:
                    self._session_index.pop(sid, None)
                    self._turn_counter.pop(sid, None)
                else:
                    self._turn_counter[sid] = len(self._session_index[sid])
            logger.info("Cleaned up %d expired turns across all sessions", evicted)
        return evicted

    def save_feedback(
        self,
        session_id: str,
        turn_id: int,
        rating: str,
        *,
        flagged_citation_indices: tuple[int, ...] = (),
        comment: str = "",
    ) -> None:
        """Save user feedback for a specific turn."""
        if self._store is not None:
            self._store.save_feedback(
                session_id, turn_id, rating,
                flagged_citation_indices=flagged_citation_indices,
                comment=comment,
            )
            return
        entry = {
            "session_id": session_id,
            "turn_id": turn_id,
            "rating": rating,
            "flagged_citation_indices": list(flagged_citation_indices),
            "comment": comment,
            "timestamp": time.time(),
        }
        self._feedback.append(entry)

    def get_feedback(self, session_id: str) -> list[dict]:
        """Get all feedback for a session."""
        if self._store is not None:
            return self._store.get_feedback(session_id)
        return [f for f in self._feedback if f["session_id"] == session_id]

    def list_sessions(self) -> list[dict]:
        """List all unique sessions with their latest turn info."""
        if self._store is not None:
            return self._store.list_sessions()
        sessions: dict[str, dict] = {}
        for turn in self._turns:
            sid = turn["session_id"]
            if sid not in sessions or turn["turn_id"] > sessions[sid]["turn_id"]:
                sessions[sid] = {
                    "session_id": sid,
                    "turn_id": turn["turn_id"],
                    "last_question": turn["question"],
                    "timestamp": turn["timestamp"],
                }
        return sorted(sessions.values(), key=lambda s: s["timestamp"], reverse=True)
