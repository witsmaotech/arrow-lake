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
