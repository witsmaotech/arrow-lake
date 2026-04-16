"""Integration tests for data lineage store — Story 8.3.

Tests lineage event persistence and querying over real Lance datasets.
"""

from __future__ import annotations

from pathlib import Path

from arrow_lake.catalog.lineage import (
    LineageQueryBridge,
    LineageStore,
    create_lineage_event,
)
from arrow_lake.ingest.storage import LanceStorageManager


class TestRecordAndRetrieve:
    """Test basic lineage event record and retrieval."""

    def test_record_and_retrieve(self, tmp_path: Path) -> None:
        """Create LineageStore, record event, verify via get_dataset_history."""
        store = LineageStore(LanceStorageManager(str(tmp_path)))
        event = create_lineage_event(
            dataset_name="test_ds",
            operation="create",
            source_datasets=["source_a"],
        )
        store.record_event(event)

        history = store.get_dataset_history("test_ds")
        assert len(history) == 1
        assert history[0].event_id == event.event_id
        assert history[0].operation == "create"
        assert "source_a" in history[0].source_datasets

    def test_multiple_events(self, tmp_path: Path) -> None:
        """Record 3 events for same dataset, verify history returns all 3."""
        store = LineageStore(LanceStorageManager(str(tmp_path)))

        events = [
            create_lineage_event("test_ds", "create"),
            create_lineage_event("test_ds", "append"),
            create_lineage_event("test_ds", "transform", transform_type="dedup"),
        ]
        for e in events:
            store.record_event(e)

        history = store.get_dataset_history("test_ds")
        assert len(history) == 3
        operations = [h.operation for h in history]
        assert operations == ["create", "append", "transform"]

    def test_get_history_empty_dataset(self, tmp_path: Path) -> None:
        """get_dataset_history returns empty list for unknown dataset."""
        store = LineageStore(LanceStorageManager(str(tmp_path)))
        store.record_event(create_lineage_event("other_ds", "create"))

        history = store.get_dataset_history("nonexistent")
        assert history == []


class TestLineageQueryBridge:
    """Test SQL query interface over lineage events."""

    def test_sql_query_over_lineage(self, tmp_path: Path) -> None:
        """SELECT * returns Arrow table with correct rows."""
        store = LineageStore(LanceStorageManager(str(tmp_path)))
        store.record_event(create_lineage_event("ds_a", "create"))
        store.record_event(create_lineage_event("ds_b", "append"))

        bridge = LineageQueryBridge(store)
        result = bridge.query("SELECT * FROM lineage")

        assert result.num_rows == 2
        dataset_names = result.column("dataset_name").to_pylist()
        assert "ds_a" in dataset_names
        assert "ds_b" in dataset_names


class TestUpstreamTrace:
    """Test upstream/downstream lineage tracing."""

    def test_upstream_trace(self, tmp_path: Path) -> None:
        """Create dataset A, then B with source A. trace_upstream('A') finds B."""
        store = LineageStore(LanceStorageManager(str(tmp_path)))
        store.record_event(create_lineage_event("dataset_a", "create"))
        store.record_event(
            create_lineage_event(
                "dataset_b",
                "transform",
                source_datasets=["dataset_a"],
                transform_type="enrich",
            ),
        )

        bridge = LineageQueryBridge(store)
        upstream = bridge.trace_upstream("dataset_a")

        assert len(upstream) == 1
        assert upstream[0].dataset_name == "dataset_b"
        assert upstream[0].transform_type == "enrich"

    def test_upstream_trace_no_dependents(self, tmp_path: Path) -> None:
        """trace_upstream returns empty when nothing depends on the dataset."""
        store = LineageStore(LanceStorageManager(str(tmp_path)))
        store.record_event(create_lineage_event("orphan_ds", "create"))

        bridge = LineageQueryBridge(store)
        upstream = bridge.trace_upstream("orphan_ds")

        assert upstream == []

    def test_downstream_trace(self, tmp_path: Path) -> None:
        """trace_downstream('B') finds events where B has upstream sources."""
        store = LineageStore(LanceStorageManager(str(tmp_path)))
        store.record_event(create_lineage_event("dataset_a", "create"))
        store.record_event(
            create_lineage_event(
                "dataset_b",
                "transform",
                source_datasets=["dataset_a"],
            ),
        )

        bridge = LineageQueryBridge(store)
        downstream = bridge.trace_downstream("dataset_b")

        assert len(downstream) == 1
        assert downstream[0].source_datasets == ("dataset_a",)
