"""RAG session history management."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict

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
    ) -> None:
        self._history_dataset = history_dataset
        self._max_sessions = max_sessions
        self._max_turns_per_session = max_turns_per_session
        self._turns: list[dict] = []
        self._turn_counter: dict[str, int] = {}
        self._session_index: dict[str, list[dict]] = {}

    def save_turn(
        self,
        session_id: str,
        question: str,
        response: object,
    ) -> None:
        """Save a conversation turn."""
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
            oldest = sorted(
                (sid, min(t["timestamp"] for t in self._turns if t["session_id"] == sid))
                for sid in self._turn_counter
            )
            while len(self._turn_counter) > self._max_sessions and oldest:
                evict_sid, _ = oldest.pop(0)
                self._turns = [t for t in self._turns if t["session_id"] != evict_sid]
                self._session_index.pop(evict_sid, None)
                self._turn_counter.pop(evict_sid, None)
                logger.warning("Evicted session %s (max_sessions=%d)", evict_sid, self._max_sessions)

    def get_history(self, session_id: str) -> list[dict]:
        """Get conversation history for a session, sorted by turn_id."""
        return sorted(
            self._session_index.get(session_id, []),
            key=lambda t: t["turn_id"],
        )

    def delete_session(self, session_id: str) -> None:
        """Delete all turns for a session."""
        self._turns = [t for t in self._turns if t["session_id"] != session_id]
        self._turn_counter.pop(session_id, None)
        self._session_index.pop(session_id, None)

    def list_sessions(self) -> list[dict]:
        """List all unique sessions with their latest turn info."""
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
