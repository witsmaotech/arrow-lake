"""Ingest dead-letter queue for failed document processing.

Tracks documents that fail during ingestion and provides retry/inspection
capabilities. Failed items are persisted to a JSON log file.

Usage:
    dlq = IngestDeadLetterQueue(base_dir="./data")
    dlq.add("report.pdf", error="PDF parsing failed: corrupted header")
    items = dlq.list_items(status="pending")
    dlq.retry("report.pdf")
    dlq.purge(resolved=True)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any


class DLQStatus(str, Enum):
    PENDING = "pending"
    RETRYING = "retrying"
    RESOLVED = "resolved"
    PERMANENT = "permanent"


@dataclass
class DeadLetterItem:
    file_path: str
    error: str
    status: str = DLQStatus.PENDING.value
    dataset: str = ""
    attempt_count: int = 1
    max_retries: int = 3
    first_failed_at: str = ""
    last_failed_at: str = ""
    last_error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.first_failed_at:
            ts = datetime.now(UTC).isoformat()
            self.first_failed_at = ts
            self.last_failed_at = ts
            self.last_error = self.error

    @property
    def can_retry(self) -> bool:
        return self.status in (DLQStatus.PENDING.value, DLQStatus.RETRYING.value) and self.attempt_count < self.max_retries

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeadLetterItem:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class IngestDeadLetterQueue:
    """Persists failed ingestion items to a JSON-backed queue.

    Thread safety: NOT safe for concurrent writes. Use external locking
    if needed (e.g., file locks or database transactions).
    """

    def __init__(self, base_dir: str | Path = "./data", queue_file: str = "ingest_dlq.jsonl") -> None:
        self._queue_path = Path(base_dir) / queue_file
        self._queue_path.parent.mkdir(parents=True, exist_ok=True)
        self._items: list[DeadLetterItem] = []
        self._load()

    def add(self, file_path: str, error: str, *, dataset: str = "", metadata: dict[str, Any] | None = None) -> None:
        item = DeadLetterItem(
            file_path=file_path,
            error=error,
            dataset=dataset,
            metadata=metadata or {},
        )
        self._items.append(item)
        self._append_item(item)
        return None

    def retry(self, file_path: str) -> bool:
        for item in self._items:
            if item.file_path == file_path and item.can_retry:
                item.status = DLQStatus.RETRYING.value
                item.attempt_count += 1
                item.last_failed_at = datetime.now(UTC).isoformat()
                self._save()
                return True
        return False

    def resolve(self, file_path: str) -> bool:
        for item in self._items:
            if item.file_path == file_path and item.status != DLQStatus.RESOLVED.value:
                item.status = DLQStatus.RESOLVED.value
                self._save()
                return True
        return False

    def mark_permanent(self, file_path: str, *, reason: str = "") -> bool:
        for item in self._items:
            if item.file_path == file_path:
                item.status = DLQStatus.PERMANENT.value
                if reason:
                    item.last_error = reason
                self._save()
                return True
        return False

    def list_items(self, *, status: str | None = None, dataset: str | None = None) -> list[DeadLetterItem]:
        items = self._items
        if status:
            items = [i for i in items if i.status == status]
        if dataset:
            items = [i for i in items if i.dataset == dataset]
        return items

    def purge(self, *, resolved: bool = False, permanent: bool = False) -> int:
        before = len(self._items)
        if resolved:
            self._items = [i for i in self._items if i.status != DLQStatus.RESOLVED.value]
        if permanent:
            self._items = [i for i in self._items if i.status != DLQStatus.PERMANENT.value]
        removed = before - len(self._items)
        if removed > 0:
            self._save()
        return removed

    @property
    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self._items:
            counts[item.status] = counts.get(item.status, 0) + 1
        counts["total"] = len(self._items)
        return counts

    def _load(self) -> None:
        if not self._queue_path.exists():
            return
        try:
            text = self._queue_path.read_text(encoding="utf-8")
            for line in text.strip().split("\n"):
                if line.strip():
                    self._items.append(DeadLetterItem.from_dict(json.loads(line)))
        except (json.JSONDecodeError, ValueError, OSError):
            pass

    def _append_item(self, item: DeadLetterItem) -> None:
        with open(self._queue_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")

    def _save(self) -> None:
        with open(self._queue_path, "w", encoding="utf-8") as f:
            for item in self._items:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
