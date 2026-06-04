"""Comprehensive tests for catalog/lineage.py — targeting uncovered paths.

Focus on:
- LineageStore.record_event with _sync_lineage_to_gravitino
- LineageStore._notify_gravitino_version (with/without env vars, auth provider)
- LineageStore._sync_lineage_to_gravitino (with/without env vars, no source_datasets, auth provider)
- LineageStore.get_dataset_history (empty store, storage error)
- LineageStore._ensure_store (lazy init, already initialized)
- LineageQueryBridge.query (with session_manager branch)
- LineageQueryBridge.trace_full_graph (diamond graph, cycles)
- LineageQueryBridge.trace_impact (complex cascading)
- create_lineage_event factory edge cases
- SQL validation edge cases
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from arrow_lake.catalog.lineage import (
    ColumnMapping,
    LineageEvent,
    LineageQueryBridge,
    LineageStore,
    create_lineage_event,
)
from arrow_lake.exceptions import CatalogError, ErrorCode
from arrow_lake.ingest.storage import LanceStorageManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    dataset_name: str = "target_ds",
    operation: str = "transform",
    source_datasets: tuple[str, ...] = ("src_ds",),
    transform_type: str = "etl",
    lance_version: int | None = None,
    actor: str = "test",
    metadata: tuple[tuple[str, object], ...] = (),
) -> LineageEvent:
    return LineageEvent(
        event_id="test-id-1",
        timestamp="2026-01-01T00:00:00",
        dataset_name=dataset_name,
        operation=operation,
        source_datasets=source_datasets,
        transform_type=transform_type,
        lance_version=lance_version,
        actor=actor,
        metadata=metadata,
    )


def _make_store(tmp_path: Path) -> LineageStore:
    storage = LanceStorageManager(str(tmp_path))
    return LineageStore(storage)


# ---------------------------------------------------------------------------
# _sync_lineage_to_gravitino
# ---------------------------------------------------------------------------


class TestSyncLineageToGravitino:
    """Test best-effort lineage sync to Gravitino Lineage REST API."""

    def test_no_env_vars_is_noop(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = _make_event()
        # Should not raise even without env vars
        store._sync_lineage_to_gravitino(event)

    def test_no_source_datasets_skips(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = _make_event(source_datasets=())

        with patch.dict(os.environ, {"ARROW_LAKE__GRAVITINO__URI": "http://gravitino:8090"}):
            store._sync_lineage_to_gravitino(event)
            # Should return early without making HTTP call

    def test_sync_success_with_env_vars(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = _make_event()

        with patch.dict(os.environ, {
            "ARROW_LAKE__GRAVITINO__URI": "http://gravitino:8090",
            "ARROW_LAKE__GRAVITINO__METALAKE": "test_metalake",
            "ARROW_LAKE__GRAVITINO__LANCE_CATALOG_NAME": "lance-cat",
            "ARROW_LAKE__GRAVITINO__LANCE_SCHEMA_NAME": "test_schema",
        }):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.__enter__ = MagicMock(return_value=mock_resp)
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = mock_resp

                store._sync_lineage_to_gravitino(event)

                mock_urlopen.assert_called_once()
                # Verify the request was constructed correctly
                req = mock_urlopen.call_args[0][0]
                assert req.method == "POST"
                assert "/lineage" in req.full_url
                assert "test_metalake" in req.full_url

    def test_sync_with_auth_provider(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        mock_auth = MagicMock()
        store.set_auth_provider(mock_auth)
        event = _make_event()

        with patch.dict(os.environ, {"ARROW_LAKE__GRAVITINO__URI": "http://gravitino:8090"}):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.__enter__ = MagicMock(return_value=mock_resp)
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = mock_resp

                store._sync_lineage_to_gravitino(event)
                mock_auth.authenticate.assert_called_once()

    def test_sync_http_failure_does_not_raise(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = _make_event()

        with patch.dict(os.environ, {"ARROW_LAKE__GRAVITINO__URI": "http://gravitino:8090"}):
            with patch("urllib.request.urlopen", side_effect=Exception("network error")):
                # Should not raise — best-effort
                store._sync_lineage_to_gravitino(event)

    def test_sync_payload_structure(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = _make_event(
            source_datasets=("src1", "src2"),
            transform_type="etl",
            operation="transform",
            actor="user",
        )

        captured_body = {}

        with patch.dict(os.environ, {
            "ARROW_LAKE__GRAVITINO__URI": "http://gravitino:8090",
        }):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.__enter__ = MagicMock(return_value=mock_resp)
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = mock_resp

                store._sync_lineage_to_gravitino(event)

                req = mock_urlopen.call_args[0][0]
                body = json.loads(req.data.decode())
                assert len(body["upstream"]) == 2
                assert body["upstream"][0]["table"] == "src1"
                assert body["transformation"] == "etl"
                assert body["properties"]["operation"] == "transform"
                assert body["properties"]["actor"] == "user"


# ---------------------------------------------------------------------------
# _notify_gravitino_version
# ---------------------------------------------------------------------------


class TestNotifyGravitinoVersion:
    """Test Lance version notification to Gravitino."""

    def test_no_env_vars_is_noop(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = _make_event(lance_version=5)
        store._notify_gravitino_version(event)

    def test_no_lance_rest_uri_is_noop(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = _make_event(lance_version=5)

        with patch.dict(os.environ, {"ARROW_LAKE__GRAVITINO__LANCE_REST_URI": ""}):
            store._notify_gravitino_version(event)

    def test_no_gravitino_uri_is_noop(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = _make_event(lance_version=5)

        with patch.dict(os.environ, {
            "ARROW_LAKE__GRAVITINO__LANCE_REST_URI": "http://lance:9101",
            "ARROW_LAKE__GRAVITINO__URI": "",
        }):
            store._notify_gravitino_version(event)

    def test_notify_success(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = _make_event(lance_version=10, dataset_name="my_table")

        with patch.dict(os.environ, {
            "ARROW_LAKE__GRAVITINO__LANCE_REST_URI": "http://lance:9101",
            "ARROW_LAKE__GRAVITINO__URI": "http://gravitino:8090",
            "ARROW_LAKE__GRAVITINO__METALAKE": "ml",
            "ARROW_LAKE__GRAVITINO__LANCE_CATALOG_NAME": "lance-cat",
            "ARROW_LAKE__GRAVITINO__LANCE_SCHEMA_NAME": "test_schema",
        }):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.__enter__ = MagicMock(return_value=mock_resp)
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = mock_resp

                store._notify_gravitino_version(event)

                req = mock_urlopen.call_args[0][0]
                assert req.method == "PUT"
                assert "my_table" in req.full_url
                body = json.loads(req.data.decode())
                assert any(
                    u["property"] == "lance.latest_version" and u["value"] == "10"
                    for u in body["updates"]
                )

    def test_notify_with_auth_provider(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        mock_auth = MagicMock()
        store.set_auth_provider(mock_auth)
        event = _make_event(lance_version=3)

        with patch.dict(os.environ, {
            "ARROW_LAKE__GRAVITINO__LANCE_REST_URI": "http://lance:9101",
            "ARROW_LAKE__GRAVITINO__URI": "http://gravitino:8090",
        }):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.__enter__ = MagicMock(return_value=mock_resp)
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = mock_resp

                store._notify_gravitino_version(event)
                mock_auth.authenticate.assert_called_once()

    def test_notify_http_failure_does_not_raise(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = _make_event(lance_version=5)

        with patch.dict(os.environ, {
            "ARROW_LAKE__GRAVITINO__LANCE_REST_URI": "http://lance:9101",
            "ARROW_LAKE__GRAVITINO__URI": "http://gravitino:8090",
        }):
            with patch("urllib.request.urlopen", side_effect=Exception("fail")):
                store._notify_gravitino_version(event)  # no raise

    def test_notify_with_datetime_timestamp(self, tmp_path: Path) -> None:
        """Test timestamp handling when event.timestamp is a datetime object."""
        from datetime import datetime, UTC
        store = _make_store(tmp_path)
        event = LineageEvent(
            event_id="e1",
            timestamp=datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC).isoformat(),
            dataset_name="ds",
            operation="create",
            source_datasets=(),
            transform_type="",
            lance_version=5,
            actor="system",
            metadata=(),
        )

        with patch.dict(os.environ, {
            "ARROW_LAKE__GRAVITINO__LANCE_REST_URI": "http://lance:9101",
            "ARROW_LAKE__GRAVITINO__URI": "http://gravitino:8090",
        }):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.__enter__ = MagicMock(return_value=mock_resp)
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = mock_resp

                store._notify_gravitino_version(event)
                # Verify the lineage.timestamp property was included
                req = mock_urlopen.call_args[0][0]
                body = json.loads(req.data.decode())
                ts_updates = [u for u in body["updates"] if u["property"] == "lineage.timestamp"]
                assert len(ts_updates) == 1


# ---------------------------------------------------------------------------
# record_event integration
# ---------------------------------------------------------------------------


class TestRecordEventIntegration:
    """Test record_event with Gravitino sync."""

    def test_record_event_triggers_gravitino_sync(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = _make_event()

        with patch.object(store, "_sync_lineage_to_gravitino") as mock_sync:
            with patch.dict(os.environ, {}, clear=True):
                store.record_event(event)
                mock_sync.assert_called_once_with(event)

    def test_record_event_with_lance_version_triggers_notify(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = _make_event(lance_version=7)

        with patch.object(store, "_notify_gravitino_version") as mock_notify:
            with patch.object(store, "_sync_lineage_to_gravitino"):
                with patch.dict(os.environ, {}, clear=True):
                    store.record_event(event)
                    mock_notify.assert_called_once_with(event)

    def test_record_event_without_lance_version_skips_notify(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = _make_event(lance_version=None)

        with patch.object(store, "_notify_gravitino_version") as mock_notify:
            with patch.object(store, "_sync_lineage_to_gravitino"):
                with patch.dict(os.environ, {}, clear=True):
                    store.record_event(event)
                    mock_notify.assert_not_called()

    def test_record_event_storage_failure_raises_catalog_error(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # Initialize the store first, then make append fail
        store._initialized = True
        mock_storage = MagicMock()
        mock_storage.append_dataset.side_effect = OSError("disk full")
        store._storage = mock_storage

        event = _make_event()
        with pytest.raises(CatalogError) as exc_info:
            store.record_event(event)
        assert exc_info.value.error_code == ErrorCode.LINEAGE_STORE_FAILED


# ---------------------------------------------------------------------------
# get_dataset_history
# ---------------------------------------------------------------------------


class TestGetDatasetHistory:
    """Test retrieving dataset history from store."""

    def test_empty_store_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # Ensure store is initialized
        store._ensure_store()
        result = store.get_dataset_history("nonexistent")
        assert result == []

    def test_storage_error_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        mock_storage = MagicMock()
        mock_storage.dataset_exists.return_value = True
        mock_storage.read_dataset.side_effect = OSError("read error")
        store._storage = mock_storage
        store._initialized = True

        result = store.get_dataset_history("ds")
        assert result == []

    def test_returns_matching_events(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event1 = create_lineage_event("ds_a", "create", actor="test")
        event2 = create_lineage_event("ds_b", "create", actor="test")
        event3 = create_lineage_event("ds_a", "append", actor="test")

        with patch.object(store, "_sync_lineage_to_gravitino"):
            with patch.object(store, "_notify_gravitino_version"):
                with patch.dict(os.environ, {}, clear=True):
                    store.record_event(event1)
                    store.record_event(event2)
                    store.record_event(event3)

        history = store.get_dataset_history("ds_a")
        assert len(history) == 2
        assert all(e.dataset_name == "ds_a" for e in history)


# ---------------------------------------------------------------------------
# _ensure_store
# ---------------------------------------------------------------------------


class TestEnsureStore:
    """Test lazy store initialization."""

    def test_creates_store_on_first_call(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store._initialized is False
        store._ensure_store()
        assert store._initialized is True
        assert store._storage.dataset_exists("_lineage_events")

    def test_skips_creation_if_already_initialized(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store._ensure_store()

        # Second call should not create again
        mock_storage = MagicMock()
        store._storage = mock_storage
        store._ensure_store()
        mock_storage.create_dataset.assert_not_called()

    def test_skips_creation_if_dataset_exists(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # Create the dataset manually
        empty_table = pa.table(
            {f.name: [] for f in pa.schema([
                pa.field("event_id", pa.string()),
                pa.field("timestamp", pa.string()),
                pa.field("dataset_name", pa.string()),
                pa.field("operation", pa.string()),
                pa.field("source_datasets", pa.string()),
                pa.field("transform_type", pa.string()),
                pa.field("lance_version", pa.int64()),
                pa.field("actor", pa.string()),
                pa.field("metadata", pa.string()),
                pa.field("column_lineage", pa.string()),
            ])},
        )
        store._storage.create_dataset("_lineage_events", empty_table)

        store._ensure_store()
        assert store._initialized is True


# ---------------------------------------------------------------------------
# LineageQueryBridge.query with session_manager
# ---------------------------------------------------------------------------


class TestLineageQueryBridgeSessionManager:
    """Test query with session_manager branch."""

    def test_query_with_session_manager(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # Record an event first
        event = create_lineage_event("test_ds", "create", actor="test")
        with patch.object(store, "_sync_lineage_to_gravitino"):
            with patch.object(store, "_notify_gravitino_version"):
                with patch.dict(os.environ, {}, clear=True):
                    store.record_event(event)

        # Create a mock session manager
        mock_conn = MagicMock()
        mock_reader = MagicMock()
        mock_reader.read_all.return_value = pa.table({
            "event_id": ["e1"],
            "timestamp": ["2026-01-01T00:00:00"],
            "dataset_name": ["test_ds"],
            "operation": ["create"],
            "source_datasets": ["[]"],
            "transform_type": [""],
            "lance_version": [None],
            "actor": ["test"],
            "metadata": ["{}"],
            "column_lineage": [None],
        })
        mock_conn.execute.return_value.arrow.return_value = mock_reader

        mock_session_mgr = MagicMock()
        mock_session_mgr.acquire.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_session_mgr.acquire.return_value.__exit__ = MagicMock(return_value=False)

        bridge = LineageQueryBridge(store, session_manager=mock_session_mgr)
        result = bridge.query("SELECT * FROM _lineage_events")

        assert result.num_rows == 1
        mock_session_mgr.acquire.assert_called_once()

    def test_query_without_session_manager(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        event = create_lineage_event("test_ds", "create", actor="test")

        with patch.object(store, "_sync_lineage_to_gravitino"):
            with patch.object(store, "_notify_gravitino_version"):
                with patch.dict(os.environ, {}, clear=True):
                    store.record_event(event)

        bridge = LineageQueryBridge(store)
        # Uses DuckDBSession directly
        result = bridge.query("SELECT * FROM _lineage_events")
        assert result.num_rows == 1


# ---------------------------------------------------------------------------
# trace_full_graph — advanced scenarios
# ---------------------------------------------------------------------------


class TestTraceFullGraphAdvanced:
    """Advanced graph tracing scenarios."""

    def test_diamond_graph_deduplicates_edges(self) -> None:
        """A -> B, A -> C -> B should not duplicate B's edge from A."""
        store = MagicMock()
        bridge = LineageQueryBridge(store)

        bridge.trace_upstream = MagicMock(return_value=[])
        bridge.trace_downstream = MagicMock(
            side_effect=lambda name: {
                "A": [
                    _make_event("B", source_datasets=("A",)),
                    _make_event("C", source_datasets=("A",)),
                ],
                "B": [],
                "C": [_make_event("B", source_datasets=("C",))],
            }.get(name, [])
        )

        result = bridge.trace_full_graph("A")
        # Check that edge A->B appears only once
        edges_from_a_to_b = [
            e for e in result["edges"]
            if e["from"] == "A" and e["to"] == "B"
        ]
        assert len(edges_from_a_to_b) == 1

    def test_cyclic_graph_does_not_loop(self) -> None:
        """A -> B -> A should not infinite loop."""
        store = MagicMock()
        bridge = LineageQueryBridge(store)

        bridge.trace_upstream = MagicMock(return_value=[])
        bridge.trace_downstream = MagicMock(
            side_effect=lambda name: {
                "A": [_make_event("B", source_datasets=("A",))],
                "B": [_make_event("A", source_datasets=("B",))],
            }.get(name, [])
        )

        result = bridge.trace_full_graph("A")
        # Should terminate without hanging
        assert result["stats"]["total_nodes"] >= 1

    def test_full_graph_includes_upstream_and_downstream(self) -> None:
        """Starting from middle node, should trace both directions."""
        store = MagicMock()
        bridge = LineageQueryBridge(store)

        # upstream: A -> B (target)
        # downstream: B (target) -> C
        bridge.trace_upstream = MagicMock(
            side_effect=lambda name: {
                "B": [_make_event("B", source_datasets=("A",))],
                "A": [],
            }.get(name, [])
        )
        bridge.trace_downstream = MagicMock(
            side_effect=lambda name: {
                "B": [_make_event("C", source_datasets=("B",))],
                "C": [],
            }.get(name, [])
        )

        result = bridge.trace_full_graph("B")
        node_ids = {n["id"] for n in result["nodes"]}
        assert "A" in node_ids  # upstream
        assert "B" in node_ids  # target
        assert "C" in node_ids  # downstream
        assert result["stats"]["total_nodes"] == 3

    def test_graph_stats_max_depth(self) -> None:
        store = MagicMock()
        bridge = LineageQueryBridge(store)

        bridge.trace_upstream = MagicMock(return_value=[])
        bridge.trace_downstream = MagicMock(
            side_effect=lambda name: (
                [_make_event(f"{name}_d", source_datasets=(name,))]
                if name == "root"
                else []
            )
        )

        result = bridge.trace_full_graph("root", max_depth=1)
        assert result["stats"]["max_depth"] <= 1

    def test_empty_graph_stats_zero_depth(self) -> None:
        store = MagicMock()
        bridge = LineageQueryBridge(store)
        bridge.trace_upstream = MagicMock(return_value=[])
        bridge.trace_downstream = MagicMock(return_value=[])

        result = bridge.trace_full_graph("solo")
        assert result["stats"]["max_depth"] == 0
        assert result["stats"]["total_edges"] == 0


# ---------------------------------------------------------------------------
# SQL validation edge cases
# ---------------------------------------------------------------------------


class TestSQLValidationEdgeCases:
    """Additional SQL validation tests."""

    def test_whitespace_only_sql_rejected(self) -> None:
        with pytest.raises(CatalogError, match="must not be empty"):
            LineageQueryBridge._validate_sql("   ")

    def test_multiline_comment_stripped(self) -> None:
        # Should pass: dangerous keyword inside a comment is stripped
        LineageQueryBridge._validate_sql(
            "SELECT * FROM t /* DROP TABLE should be ignored */"
        )

    def test_select_with_where_passes(self) -> None:
        LineageQueryBridge._validate_sql(
            "SELECT * FROM t WHERE dataset_name = 'test'"
        )

    def test_grant_keyword_rejected(self) -> None:
        with pytest.raises(CatalogError, match="not allowed"):
            LineageQueryBridge._validate_sql("SELECT * FROM t; GRANT ALL ON t TO public")

    def test_copy_keyword_rejected(self) -> None:
        with pytest.raises(CatalogError, match="not allowed"):
            LineageQueryBridge._validate_sql("SELECT * FROM t COPY t TO '/tmp/x'")


# ---------------------------------------------------------------------------
# create_lineage_event factory
# ---------------------------------------------------------------------------


class TestCreateLineageEventFactory:
    """Test the factory function for creating lineage events."""

    def test_auto_generated_fields(self) -> None:
        event = create_lineage_event("ds", "create")
        assert event.dataset_name == "ds"
        assert event.operation == "create"
        assert event.actor == "system"
        assert len(event.event_id) > 0
        assert len(event.timestamp) > 0
        assert event.source_datasets == ()
        assert event.transform_type == ""
        assert event.lance_version is None
        assert event.metadata == ()

    def test_all_fields_populated(self) -> None:
        event = create_lineage_event(
            "target",
            "transform",
            source_datasets=["s1", "s2"],
            transform_type="etl",
            lance_version=42,
            actor="pipeline",
            metadata={"key": "value", "count": 10},
        )
        assert event.source_datasets == ("s1", "s2")
        assert event.lance_version == 42
        assert event.actor == "pipeline"
        # Metadata is sorted tuple
        assert event.metadata == (("count", 10), ("key", "value"))

    def test_metadata_sorted_consistently(self) -> None:
        event = create_lineage_event(
            "ds", "create",
            metadata={"z_key": 1, "a_key": 2},
        )
        keys = [k for k, v in event.metadata]
        assert keys == ["a_key", "z_key"]

    def test_each_call_unique_id(self) -> None:
        e1 = create_lineage_event("ds", "create")
        e2 = create_lineage_event("ds", "create")
        assert e1.event_id != e2.event_id


# ---------------------------------------------------------------------------
# _row_to_event
# ---------------------------------------------------------------------------


class TestRowToEvent:
    """Test static row-to-event conversion."""

    def test_basic_conversion(self) -> None:
        table = pa.table({
            "event_id": ["e1"],
            "timestamp": ["2026-01-01T00:00:00"],
            "dataset_name": ["ds"],
            "operation": ["create"],
            "source_datasets": ['["src1", "src2"]'],
            "transform_type": ["etl"],
            "lance_version": [5],
            "actor": ["user"],
            "metadata": ['{"key": "val"}'],
            "column_lineage": [None],
        })
        event = LineageStore._row_to_event(table, 0)
        assert event.event_id == "e1"
        assert event.dataset_name == "ds"
        assert event.source_datasets == ("src1", "src2")
        assert event.lance_version == 5
        assert event.metadata == (("key", "val"),)

    def test_null_source_datasets(self) -> None:
        table = pa.table({
            "event_id": ["e1"],
            "timestamp": ["2026-01-01T00:00:00"],
            "dataset_name": ["ds"],
            "operation": ["create"],
            "source_datasets": [None],
            "transform_type": [None],
            "lance_version": [None],
            "actor": [None],
            "metadata": [None],
            "column_lineage": [None],
        })
        event = LineageStore._row_to_event(table, 0)
        assert event.source_datasets == ()
        assert event.transform_type == ""
        assert event.actor == ""
        assert event.metadata == ()
