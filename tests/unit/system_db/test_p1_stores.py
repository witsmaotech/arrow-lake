"""P1 store tests (v1.9.0): catalog / task-history / DLQ / RAG-session +
the TaskManager history hook and the DLQ/SessionStore delegation paths.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from arrow_lake.api.tasks import BackgroundTask, TaskManager, TaskStatus
from arrow_lake.ingest.dead_letter import IngestDeadLetterQueue
from arrow_lake.rag.session import SessionStore
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores import (
    CatalogStore,
    IngestDLQStore,
    RagSessionStore,
    TaskHistoryStore,
)


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


# --------------------------------------------------------------------------- #
class TestCatalogStore:
    def test_register_get_list_archive_restore_delete(self, db: SystemDB) -> None:
        cs = CatalogStore(db)
        cs.register_table("ds1", "{}", "s3://ds1")
        assert cs.get_table("ds1")["status"] == "active"
        assert len(cs.list_tables()) == 1
        assert cs.archive_dataset("ds1") is True
        assert cs.get_table("ds1")["status"] == "archived"
        assert cs.list_tables() == []  # archived excluded by default
        assert cs.restore_dataset("ds1") is True
        assert cs.delete_table("ds1") is True
        assert cs.get_table("ds1") is None


# --------------------------------------------------------------------------- #
class TestTaskHistoryStore:
    def test_record_get_and_duration(self, db: SystemDB) -> None:
        th = TaskHistoryStore(db)
        th.record({
            "task_id": "t1", "operation": "export", "dataset_name": "ds1",
            "status": "completed", "progress": 1.0,
            "created_at": "2026-07-17T09:00:00+00:00",
            "completed_at": "2026-07-17T09:00:05+00:00",
            "result": {"rows": 10},
        })
        got = th.get("t1")
        assert got is not None and got["duration_ms"] == 5000
        assert got["result"] == {"rows": 10}
        assert len(th.list(operation="export")) == 1

    def test_task_manager_persists_completed_to_history(self, db: SystemDB) -> None:
        th = TaskHistoryStore(db)
        prev = TaskManager._history_store
        try:
            TaskManager.init_history_store(th)
            task = BackgroundTask(
                task_id="tm1", operation="export", dataset_name="ds1",
                status=TaskStatus.COMPLETED, progress=1.0,
                created_at="2026-07-17T09:00:00+00:00",
                completed_at="2026-07-17T09:00:10+00:00",
                result={"ok": True},
            )
            TaskManager._persist_history(task)
            assert th.get("tm1") is not None
            # pending tasks are NOT persisted
            TaskManager._persist_history(
                BackgroundTask(task_id="tm2", operation="x", status=TaskStatus.PENDING)
            )
            assert th.get("tm2") is None
        finally:
            TaskManager._history_store = prev


# --------------------------------------------------------------------------- #
class TestIngestDLQStore:
    def test_add_bumps_attempt_then_retry_resolve(self, db: SystemDB) -> None:
        dlq = IngestDLQStore(db)
        dlq.add("a.pdf", error="boom", dataset="ds1")
        dlq.add("a.pdf", error="boom2", dataset="ds1")
        item = dlq.list_items(dataset="ds1")[0]
        assert item["attempt_count"] == 2
        assert dlq.retry("a.pdf", dataset="ds1") is True
        assert dlq.resolve("a.pdf", dataset="ds1") is True
        assert dlq.stats["resolved"] == 1

    def test_purge(self, db: SystemDB) -> None:
        dlq = IngestDLQStore(db)
        dlq.add("a.pdf", "e", dataset="ds1")
        dlq.resolve("a.pdf", dataset="ds1")
        assert dlq.purge(resolved=True) == 1
        assert dlq.stats["total"] == 0

    def test_dead_letter_queue_delegates_to_store(self, db: SystemDB) -> None:
        store = IngestDLQStore(db)
        q = IngestDeadLetterQueue(dlq_store=store)
        q.add("x.pdf", error="bad", dataset="ds2")
        items = q.list_items(dataset="ds2")
        assert len(items) == 1 and items[0].file_path == "x.pdf"
        assert q.stats["total"] == 1


# --------------------------------------------------------------------------- #
@dataclass
class _Resp:
    answer: str = "ans"
    citations: list = None
    latency_ms: int = 12
    llm_usage: object = None

    def __post_init__(self) -> None:
        if self.citations is None:
            self.citations = []


class TestRagSessionStore:
    def test_turns_history_feedback_sessions(self, db: SystemDB) -> None:
        rs = RagSessionStore(db)
        rs.save_turn("s1", "q1", _Resp())
        rs.save_turn("s1", "q2", _Resp())
        hist = rs.get_history("s1")
        assert [t["turn_id"] for t in hist] == [1, 2]
        rs.save_feedback("s1", 1, "good", comment="nice")
        assert len(rs.get_feedback("s1")) == 1
        sessions = rs.list_sessions()
        assert len(sessions) == 1 and sessions[0]["session_id"] == "s1"

    def test_delete_and_cleanup(self, db: SystemDB) -> None:
        rs = RagSessionStore(db)
        rs.save_turn("s1", "q1", _Resp())
        assert rs.delete_session("s1") == 1
        assert rs.get_history("s1") == []
        # cleanup_expired with future cutoff removes nothing meaningful here
        rs.save_turn("s2", "q1", _Resp())
        old = time.time() - 10_000
        db.execute("UPDATE rag_turns SET created_at = ? WHERE session_id='s2'", (old,))
        db.commit()
        assert rs.cleanup_expired(60) >= 1

    def test_session_store_delegates_to_store(self, db: SystemDB) -> None:
        store = RagSessionStore(db)
        ss = SessionStore(session_store=store)
        ss.save_turn("s1", "q1", _Resp())
        ss.save_turn("s1", "q2", _Resp())
        assert len(ss.get_history("s1")) == 2
        ss.save_feedback("s1", 1, "good")
        assert len(ss.get_feedback("s1")) == 1
        assert len(ss.list_sessions()) == 1
