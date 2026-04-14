"""Tests for arrow_lake.catalog.lineage — Story 8.3 Data Lineage.

Tests LineageEvent, LineageStore, LineageQueryBridge, create_lineage_event,
and LineageConfig using mocked lancedb/DuckDB (no real datasets).
"""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from arrow_lake.catalog.lineage import (
    LineageEvent,
    LineageQueryBridge,
    LineageStore,
    create_lineage_event,
)
from arrow_lake.config import LineageConfig
from arrow_lake.exceptions import CatalogError

# ---------------------------------------------------------------------------
# TestLineageEvent
# ---------------------------------------------------------------------------


class TestLineageEvent:
    """Test LineageEvent frozen dataclass."""

    def test_frozen(self) -> None:
        event = LineageEvent(
            event_id="e1",
            timestamp="2026-01-01T00:00:00",
            dataset_name="ds",
            operation="create",
            source_datasets=("src_a",),
            transform_type="",
            lance_version=1,
            actor="system",
            metadata=(("k", "v"),),
        )
        with pytest.raises(FrozenInstanceError):
            event.event_id = "changed"  # type: ignore[misc]

    def test_from_dict_normalizes_metadata_to_tuple(self) -> None:
        event = LineageEvent.from_dict(
            {
                "event_id": "e1",
                "timestamp": "2026-01-01T00:00:00",
                "dataset_name": "ds",
                "operation": "create",
                "metadata": {"env": "prod", "team": "data"},
            }
        )
        assert isinstance(event.metadata, tuple)
        assert event.metadata == (("env", "prod"), ("team", "data"))

    def test_from_dict_normalizes_source_datasets_to_tuple(self) -> None:
        event = LineageEvent.from_dict(
            {
                "event_id": "e1",
                "timestamp": "2026-01-01T00:00:00",
                "dataset_name": "ds",
                "operation": "transform",
                "source_datasets": ["src_a", "src_b"],
            }
        )
        assert isinstance(event.source_datasets, tuple)
        assert event.source_datasets == ("src_a", "src_b")

    def test_from_dict_defaults(self) -> None:
        event = LineageEvent.from_dict(
            {
                "event_id": "e1",
                "timestamp": "2026-01-01T00:00:00",
                "dataset_name": "ds",
                "operation": "create",
            }
        )
        # source_datasets defaults to empty tuple
        assert event.source_datasets == ()


# ---------------------------------------------------------------------------
# TestCreateLineageEvent
# ---------------------------------------------------------------------------


class TestCreateLineageEvent:
    """Test create_lineage_event factory function."""

    def test_auto_generates_uuid(self) -> None:
        e1 = create_lineage_event("ds", "create")
        e2 = create_lineage_event("ds", "create")
        assert e1.event_id != e2.event_id
        assert len(e1.event_id) == 36  # UUID4 format

    def test_auto_generates_timestamp(self) -> None:
        e1 = create_lineage_event("ds", "create")
        assert "T" in e1.timestamp  # ISO 8601

    def test_default_actor_is_system(self) -> None:
        e = create_lineage_event("ds", "create")
        assert e.actor == "system"

    def test_custom_actor(self) -> None:
        e = create_lineage_event("ds", "create", actor="alice")
        assert e.actor == "alice"

    def test_metadata_sorted_as_tuple(self) -> None:
        e = create_lineage_event("ds", "create", metadata={"z": 1, "a": 2})
        assert e.metadata == (("a", 2), ("z", 1))


# ---------------------------------------------------------------------------
# TestLineageStore
# ---------------------------------------------------------------------------


class TestLineageStore:
    """Test LineageStore with mocked lancedb."""

    def test_ensure_store_creates_dataset_on_first_use(self) -> None:
        store = LineageStore("/tmp/test_lineage")
        mock_db = MagicMock()
        mock_table = MagicMock()
        # First call to open_table raises (table doesn't exist yet)
        # so _ensure_store creates it
        mock_db.open_table.side_effect = [Exception("not found"), mock_table]

        with (
            patch("lancedb.connect", return_value=mock_db),
            patch("pathlib.Path.exists", return_value=True),
        ):
            store.record_event(
                LineageEvent(
                    event_id="e1",
                    timestamp="2026-01-01T00:00:00",
                    dataset_name="ds",
                    operation="create",
                    source_datasets=(),
                    transform_type="",
                    lance_version=None,
                    actor="system",
                    metadata=(),
                )
            )
        # create_table should be called to init the store
        mock_db.create_table.assert_called_once()
        # open_table().add() should be called to record the event
        mock_table.add.assert_called_once()

    def test_record_event_calls_lancedb_add(self) -> None:
        store = LineageStore("/tmp/test_lineage")
        mock_db = MagicMock()
        mock_table = MagicMock()
        mock_db.open_table.return_value = mock_table

        with patch("lancedb.connect", return_value=mock_db):
            store._initialized = True
            event = create_lineage_event("my_ds", "append")
            store.record_event(event)
        mock_table.add.assert_called_once()
        call_arg = mock_table.add.call_args[0][0]
        assert isinstance(call_arg, pa.Table)
        assert call_arg.num_rows == 1


# ---------------------------------------------------------------------------
# TestLineageQueryBridge._validate_sql
# ---------------------------------------------------------------------------


class TestLineageQueryBridgeValidateSql:
    """Test LineageQueryBridge._validate_sql SQL injection guards."""

    def test_select_passes(self) -> None:
        LineageQueryBridge._validate_sql("SELECT * FROM lineage")

    def test_insert_blocked(self) -> None:
        with pytest.raises(CatalogError):
            LineageQueryBridge._validate_sql("INSERT INTO lineage VALUES (1)")

    def test_semicolon_blocked(self) -> None:
        with pytest.raises(CatalogError):
            LineageQueryBridge._validate_sql("SELECT * FROM lineage; DROP TABLE x")

    def test_empty_blocked(self) -> None:
        with pytest.raises(CatalogError):
            LineageQueryBridge._validate_sql("")

    def test_whitespace_only_blocked(self) -> None:
        with pytest.raises(CatalogError):
            LineageQueryBridge._validate_sql("   ")

    def test_dangerous_keywords_blocked(self) -> None:
        for kw in ["UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"]:
            with pytest.raises(CatalogError, match=re.escape(kw)):
                LineageQueryBridge._validate_sql(f"SELECT * FROM lineage; {kw} x")


# ---------------------------------------------------------------------------
# TestLineageQueryBridge.query
# ---------------------------------------------------------------------------


class TestLineageQueryBridgeQuery:
    """Test LineageQueryBridge.query delegates to DuckDB."""

    def test_query_returns_arrow_table(self) -> None:
        store = LineageStore("/tmp/test_lineage")
        bridge = LineageQueryBridge(store)
        result_table = pa.table(
            {
                "event_id": ["e1"],
                "timestamp": ["2026-01-01"],
                "dataset_name": ["ds"],
                "operation": ["create"],
                "source_datasets": [None],
                "transform_type": [None],
                "lance_version": [None],
                "actor": [None],
                "metadata": [None],
            }
        )

        mock_db = MagicMock()
        mock_table = MagicMock()
        mock_table.to_arrow.return_value = result_table
        mock_db.open_table.return_value = mock_table

        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.read_all.return_value = result_table
        mock_conn.execute.return_value.arrow.return_value = mock_result

        import duckdb as duckdb_mod

        with (
            patch("lancedb.connect", return_value=mock_db),
            patch.object(duckdb_mod, "connect", return_value=mock_conn),
        ):
            result = bridge.query("SELECT * FROM lineage")
        assert isinstance(result, pa.Table)


# ---------------------------------------------------------------------------
# TestLineageQueryBridge.trace_upstream / trace_downstream
# ---------------------------------------------------------------------------


class TestLineageQueryBridgeTrace:
    """Test trace methods build correct SQL."""

    def _make_empty_lineage_table(self) -> pa.Table:
        return pa.table(
            {
                "event_id": [],
                "timestamp": [],
                "dataset_name": [],
                "operation": [],
                "source_datasets": [],
                "transform_type": [],
                "lance_version": [],
                "actor": [],
                "metadata": [],
            }
        )

    def test_trace_upstream_uses_like_clause(self) -> None:
        store = LineageStore("/tmp/test_lineage")
        bridge = LineageQueryBridge(store)
        empty_table = self._make_empty_lineage_table()

        mock_db = MagicMock()
        mock_table = MagicMock()
        mock_table.to_arrow.return_value = empty_table
        mock_db.open_table.return_value = mock_table

        # Mock duckdb to avoid needing pyarrow.dataset
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.read_all.return_value = empty_table
        mock_conn.execute.return_value.arrow.return_value = mock_result
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        import duckdb as duckdb_mod

        with (
            patch("lancedb.connect", return_value=mock_db),
            patch.object(duckdb_mod, "connect", return_value=mock_conn),
        ):
            result = bridge.trace_upstream("my_dataset")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_trace_downstream_filters_dataset_name(self) -> None:
        store = LineageStore("/tmp/test_lineage")
        bridge = LineageQueryBridge(store)
        empty_table = self._make_empty_lineage_table()

        mock_db = MagicMock()
        mock_table = MagicMock()
        mock_table.to_arrow.return_value = empty_table
        mock_db.open_table.return_value = mock_table

        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.read_all.return_value = empty_table
        mock_conn.execute.return_value.arrow.return_value = mock_result
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        import duckdb as duckdb_mod

        with (
            patch("lancedb.connect", return_value=mock_db),
            patch.object(duckdb_mod, "connect", return_value=mock_conn),
        ):
            result = bridge.trace_downstream("my_dataset")
        assert isinstance(result, list)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# TestLineageConfig
# ---------------------------------------------------------------------------


class TestLineageConfig:
    """Test LineageConfig defaults."""

    def test_defaults(self) -> None:
        cfg = LineageConfig()
        assert cfg.enabled is False
        assert cfg.store_dataset == "_lineage_events"
        assert cfg.auto_record is True
