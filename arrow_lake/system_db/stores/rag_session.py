"""RagSessionStore — persistent RAG conversation history (replaces in-memory lists).

Backs ``rag/session.py``'s ``SessionStore`` with the ``rag_sessions`` /
``rag_turns`` / ``rag_feedback`` tables. The in-memory fallback stays for
deployments with system_db disabled. The previously-vestigial
``history_dataset`` parameter is superseded by this durable store.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any

import structlog

from arrow_lake.system_db.connection import SystemDB
from arrow_lake.system_db.stores.base import FailMode

logger = structlog.get_logger(__name__)


class RagSessionStore:
    """Persistent RAG session/turn/feedback store. Fail-soft."""

    fail_mode = FailMode.FAIL_SOFT

    def __init__(self, db: SystemDB, *, max_turns_per_session: int = 100) -> None:
        self._db = db
        self._max_turns = max_turns_per_session

    # ------------------------------------------------------------------
    def save_turn(self, session_id: str, question: str, response: object) -> dict[str, Any]:
        """Persist one conversation turn. Returns the stored turn dict."""
        answer = getattr(response, "answer", "")
        citations = getattr(response, "citations", [])
        latency_ms = getattr(response, "latency_ms", None)
        llm_usage = getattr(response, "llm_usage", None)
        now = time.time()

        with self._db.with_write() as db:
            # ensure session row exists
            db.execute(
                "INSERT INTO rag_sessions (id, updated_at) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
                (session_id, _iso(now)),
            )
            # turn_id = max + 1
            cur = db.execute(
                "SELECT COALESCE(MAX(turn_id), 0) + 1 FROM rag_turns WHERE session_id = ?",
                (session_id,),
            )
            turn_id = int(cur.fetchone()[0]) if cur is not None else 1
            db.execute(
                "INSERT INTO rag_turns "
                "(session_id, turn_id, role, question, answer, citations, latency_ms, llm_usage, created_at) "
                "VALUES (?, ?, 'user', ?, ?, ?, ?, ?, ?)",
                (
                    session_id, turn_id, question, str(answer),
                    json.dumps([asdict(c) for c in citations]) if citations else None,
                    latency_ms,
                    json.dumps(llm_usage) if llm_usage else None,
                    now,
                ),
            )
            # enforce per-session turn cap (drop oldest)
            if self._max_turns > 0 and turn_id > self._max_turns:
                db.execute(
                    "DELETE FROM rag_turns WHERE session_id = ? AND turn_id = "
                    "(SELECT MIN(turn_id) FROM rag_turns WHERE session_id = ?)",
                    (session_id, session_id),
                )

        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "question": question,
            "answer": str(answer),
            "citations": [asdict(c) for c in citations] if citations else [],
            "latency_ms": latency_ms,
            "llm_usage": llm_usage,
            "timestamp": now,
        }

    def get_history(self, session_id: str) -> list[dict[str, Any]]:
        cur = self._db.execute(
            "SELECT turn_id, question, answer, citations, latency_ms, llm_usage, created_at "
            "FROM rag_turns WHERE session_id = ? ORDER BY turn_id",
            (session_id,),
        )
        rows = cur.fetchall() if cur is not None else []
        return [_row_to_turn(session_id, r) for r in rows]

    def delete_session(self, session_id: str) -> int:
        with self._db.with_write() as db:
            db.execute("DELETE FROM rag_feedback WHERE session_id = ?", (session_id,))
            cur = db.execute("DELETE FROM rag_turns WHERE session_id = ?", (session_id,))
            turns = cur.rowcount if cur is not None else 0
            db.execute("DELETE FROM rag_sessions WHERE id = ?", (session_id,))
        return int(turns)

    def cleanup_expired(self, ttl_seconds: int) -> int:
        if ttl_seconds <= 0:
            return 0
        cutoff = time.time() - ttl_seconds
        with self._db.with_write() as db:
            cur = db.execute("DELETE FROM rag_turns WHERE created_at < ?", (cutoff,))
            return cur.rowcount if cur is not None else 0

    def save_feedback(
        self,
        session_id: str,
        turn_id: int,
        rating: str,
        *,
        flagged_citation_indices: tuple[int, ...] = (),
        comment: str = "",
    ) -> None:
        now = time.time()
        with self._db.with_write() as db:
            db.execute(
                "INSERT INTO rag_feedback "
                "(session_id, turn_id, rating, flagged_citation_indices, comment, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id, turn_id, rating,
                    json.dumps(list(flagged_citation_indices)),
                    comment, now,
                ),
            )

    def get_feedback(self, session_id: str) -> list[dict[str, Any]]:
        cur = self._db.execute(
            "SELECT turn_id, rating, flagged_citation_indices, comment, created_at "
            "FROM rag_feedback WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        rows = cur.fetchall() if cur is not None else []
        out = []
        for turn_id, rating, flagged, comment, ts in rows:
            try:
                idx = json.loads(flagged) if flagged else []
            except (ValueError, TypeError):
                idx = []
            out.append({
                "session_id": session_id, "turn_id": turn_id, "rating": rating,
                "flagged_citation_indices": idx, "comment": comment, "timestamp": ts,
            })
        return out

    def list_sessions(self) -> list[dict[str, Any]]:
        cur = self._db.execute(
            "SELECT t.session_id, MAX(t.turn_id) AS turn_id, "
            "       (SELECT question FROM rag_turns WHERE session_id=t.session_id "
            "        ORDER BY turn_id DESC LIMIT 1) AS last_question, "
            "       MAX(t.created_at) AS ts "
            "FROM rag_turns t GROUP BY t.session_id ORDER BY ts DESC"
        )
        rows = cur.fetchall() if cur is not None else []
        return [
            {"session_id": r[0], "turn_id": int(r[1] or 0),
             "last_question": r[2], "timestamp": r[3]}
            for r in rows
        ]


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _row_to_turn(session_id: str, r: tuple) -> dict[str, Any]:
    try:
        citations = json.loads(r[3]) if r[3] else []
    except (ValueError, TypeError):
        citations = []
    try:
        llm_usage = json.loads(r[5]) if r[5] else None
    except (ValueError, TypeError):
        llm_usage = None
    return {
        "session_id": session_id, "turn_id": int(r[0]), "question": r[1],
        "answer": r[2], "citations": citations, "latency_ms": r[4],
        "llm_usage": llm_usage, "timestamp": r[6],
    }


__all__ = ["RagSessionStore"]
