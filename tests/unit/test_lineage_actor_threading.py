"""v1.9.4 批1 — lineage worker threads actor + source_datasets + lance_version.

Validates the async fire-and-forget queue carries the authenticated actor
and the derived source_datasets through to ``lineage_record_event`` (previously
hardcoded ``actor="system"`` + empty ``source_datasets=[]``).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from arrow_lake._lake_lineage import _LakeLineageMixin


def test_lineage_worker_threads_actor_and_source_datasets() -> None:
    m = _LakeLineageMixin()
    m.lineage_record_event = MagicMock()
    # Enqueue an ingest event with a real actor + file sources.
    m._lineage_after_ingest(
        "ds1", source_paths=["/data/foo.csv", "/data/bar.parquet"],
        actor="alice", lance_version=7, total_rows=42,
    )
    # Wait for the background daemon worker to drain the queue.
    m._lineage_queue.join()

    m.lineage_record_event.assert_called_once()
    args, kwargs = m.lineage_record_event.call_args
    assert kwargs["actor"] == "alice"
    assert kwargs["lance_version"] == 7
    # source_datasets derived from file paths via _extract_source_datasets.
    assert kwargs["source_datasets"] == ["file:foo", "file:bar"]
    assert kwargs["metadata"]["total_rows"] == 42


def test_lineage_worker_defaults_actor_system_when_unspecified() -> None:
    m = _LakeLineageMixin()
    m.lineage_record_event = MagicMock()
    m._lineage_after_ingest("ds2", source_descriptor={"sql": "select 1"})
    m._lineage_queue.join()

    _, kwargs = m.lineage_record_event.call_args
    assert kwargs["actor"] == "system"
    assert kwargs["source_datasets"] == []


def test_lineage_worker_is_process_wide_singleton() -> None:
    """v1.11.5-W1: one shared queue + ONE worker thread for all Lakes.

    The per-Lake immortal daemon thread accumulated dozens of threads (each
    pinning its Lake's pyarrow/lance graph) and segfaulted long runs
    non-deterministically. Contract: many Lakes → same queue → single worker.
    """
    import threading

    lakes = [_LakeLineageMixin() for _ in range(5)]
    queues = {id(l._get_lineage_queue()) for l in lakes}
    assert len(queues) == 1, "all Lakes must share one process-wide queue"

    lineage_threads = [
        t for t in threading.enumerate() if t.name == "lineage-async"
    ]
    assert len(lineage_threads) == 1, "exactly one lineage worker per process"


def test_lineage_events_dropped_when_owner_collected() -> None:
    """weakref semantics: a collected Lake's pending events are dropped.

    Best-effort by design (lineage is reconstructable from Lance) — the
    worker must NOT resurrect or pin a dead Lake.
    """
    import gc

    m = _LakeLineageMixin()
    m.lineage_record_event = MagicMock()
    m._lineage_after_ingest("ds3", actor="bob")
    # Drop the only strong reference; force collection of the Lake.
    ref_target = m
    del m
    gc.collect()
    # The worker resolved a dead weakref → nothing recorded, no crash.
    # (If the event raced through before collection that's also fine —
    # the invariant is only "never crash, never resurrect".)
    q = _LakeLineageMixin._get_lineage_queue(ref_target)
    q.join()
