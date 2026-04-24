"""Tests for IngestDeadLetterQueue."""

from __future__ import annotations

import tempfile

from arrow_lake.ingest.dead_letter import DLQStatus, DeadLetterItem, IngestDeadLetterQueue


class TestDeadLetterItem:
    def test_can_retry_when_pending(self) -> None:
        item = DeadLetterItem(file_path="test.pdf", error="fail")
        assert item.can_retry is True

    def test_can_retry_false_when_max_retries_exceeded(self) -> None:
        item = DeadLetterItem(file_path="test.pdf", error="fail", attempt_count=3, max_retries=3)
        assert item.can_retry is False

    def test_can_retry_false_when_resolved(self) -> None:
        item = DeadLetterItem(file_path="test.pdf", error="fail", status=DLQStatus.RESOLVED.value)
        assert item.can_retry is False

    def test_post_init_sets_timestamps(self) -> None:
        item = DeadLetterItem(file_path="test.pdf", error="fail")
        assert item.first_failed_at != ""
        assert item.last_failed_at == item.first_failed_at

    def test_to_dict_roundtrip(self) -> None:
        item = DeadLetterItem(file_path="a.pdf", error="err", dataset="ds", metadata={"key": "val"})
        restored = DeadLetterItem.from_dict(item.to_dict())
        assert restored.file_path == "a.pdf"
        assert restored.dataset == "ds"
        assert restored.metadata == {"key": "val"}


class TestIngestDeadLetterQueue:
    def _make_dlq(self) -> IngestDeadLetterQueue:
        return IngestDeadLetterQueue(tempfile.mkdtemp())

    def test_add_and_list(self) -> None:
        dlq = self._make_dlq()
        dlq.add("bad.pdf", "corrupted")
        dlq.add("bad2.pdf", "empty", dataset="test_ds")

        items = dlq.list_items()
        assert len(items) == 2
        assert items[0].file_path == "bad.pdf"
        assert items[1].dataset == "test_ds"

    def test_list_by_status(self) -> None:
        dlq = self._make_dlq()
        dlq.add("a.pdf", "err")
        dlq.add("b.pdf", "err")
        dlq.resolve("a.pdf")

        pending = dlq.list_items(status="pending")
        assert len(pending) == 1
        assert pending[0].file_path == "b.pdf"

    def test_list_by_dataset(self) -> None:
        dlq = self._make_dlq()
        dlq.add("a.pdf", "err", dataset="ds1")
        dlq.add("b.pdf", "err", dataset="ds2")

        items = dlq.list_items(dataset="ds1")
        assert len(items) == 1

    def test_retry_increments_attempt(self) -> None:
        dlq = self._make_dlq()
        dlq.add("a.pdf", "err")

        assert dlq.retry("a.pdf") is True
        items = dlq.list_items()
        assert items[0].attempt_count == 2
        assert items[0].status == "retrying"

    def test_retry_false_when_max_exceeded(self) -> None:
        dlq = self._make_dlq()
        dlq.add("a.pdf", "err")
        dlq.retry("a.pdf")
        dlq.retry("a.pdf")

        assert dlq.retry("a.pdf") is False

    def test_retry_false_when_resolved(self) -> None:
        dlq = self._make_dlq()
        dlq.add("a.pdf", "err")
        dlq.resolve("a.pdf")

        assert dlq.retry("a.pdf") is False

    def test_resolve(self) -> None:
        dlq = self._make_dlq()
        dlq.add("a.pdf", "err")
        assert dlq.resolve("a.pdf") is True
        assert dlq.list_items(status="resolved")[0].status == "resolved"

    def test_mark_permanent(self) -> None:
        dlq = self._make_dlq()
        dlq.add("a.pdf", "err")
        assert dlq.mark_permanent("a.pdf", reason="unrecoverable") is True
        item = dlq.list_items()[0]
        assert item.status == "permanent"
        assert item.last_error == "unrecoverable"

    def test_purge_resolved(self) -> None:
        dlq = self._make_dlq()
        dlq.add("a.pdf", "err")
        dlq.add("b.pdf", "err")
        dlq.resolve("a.pdf")

        purged = dlq.purge(resolved=True)
        assert purged == 1
        assert len(dlq.list_items()) == 1

    def test_purge_permanent(self) -> None:
        dlq = self._make_dlq()
        dlq.add("a.pdf", "err")
        dlq.mark_permanent("a.pdf")

        purged = dlq.purge(permanent=True)
        assert purged == 1
        assert len(dlq.list_items()) == 0

    def test_stats(self) -> None:
        dlq = self._make_dlq()
        dlq.add("a.pdf", "err")
        dlq.add("b.pdf", "err")
        dlq.resolve("a.pdf")
        dlq.mark_permanent("b.pdf")

        stats = dlq.stats
        assert stats["total"] == 2
        assert stats["resolved"] == 1
        assert stats["permanent"] == 1

    def test_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dlq1 = IngestDeadLetterQueue(td)
            dlq1.add("a.pdf", "err1")
            dlq1.add("b.pdf", "err2", dataset="ds")

            dlq2 = IngestDeadLetterQueue(td)
            items = dlq2.list_items()
            assert len(items) == 2
            assert items[0].file_path == "a.pdf"
            assert items[1].error == "err2"

    def test_empty_queue(self) -> None:
        dlq = self._make_dlq()
        assert dlq.list_items() == []
        assert dlq.stats == {"total": 0}
        assert dlq.purge(resolved=True) == 0
