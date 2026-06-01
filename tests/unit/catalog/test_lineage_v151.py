"""Unit tests for v1.5.1 lineage enhancements — Phase 4."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from arrow_lake.catalog.lineage import (
    ColumnMapping,
    LineageEvent,
    LineageStore,
    create_lineage_event,
)
from arrow_lake.catalog.lineage_hooks import (
    auto_record_export,
    auto_record_federated,
    auto_record_ingest,
    auto_record_query,
    auto_record_rag,
    _extract_source_datasets,
)
from arrow_lake.ingest.storage import LanceStorageManager


# ---------------------------------------------------------------------------
# ColumnMapping
# ---------------------------------------------------------------------------


class TestColumnMapping:
    """Test ColumnMapping frozen dataclass."""

    def test_frozen(self) -> None:
        m = ColumnMapping(source_dataset="src", source_column="col_a", target_column="col_b")
        with pytest.raises(AttributeError):
            m.target_column = "other"  # type: ignore[misc]

    def test_default_transform_expr(self) -> None:
        m = ColumnMapping(source_dataset="src", source_column="a", target_column="b")
        assert m.transform_expr == ""

    def test_with_transform(self) -> None:
        m = ColumnMapping(
            source_dataset="src", source_column="name",
            target_column="full_name",
            transform_expr="COALESCE(src.name, 'N/A')",
        )
        assert "COALESCE" in m.transform_expr


# ---------------------------------------------------------------------------
# column_lineage in LineageStore
# ---------------------------------------------------------------------------


class TestColumnLineageSchema:
    """Test that column_lineage field is stored and retrieved."""

    def test_record_event_with_column_lineage(self, tmp_path: Path) -> None:
        store = LineageStore(LanceStorageManager(str(tmp_path)))
        event = create_lineage_event("ds", "transform", source_datasets=["src_ds"])
        mappings = [
            ColumnMapping("src_ds", "col_a", "col_b", "UPPER(col_a)"),
            ColumnMapping("src_ds", "id", "id"),
        ]

        store.record_event(event, column_lineage=mappings)

        # Verify stored data
        table = store._storage.read_dataset("_lineage_events")
        assert table.num_rows == 1
        col_lineage_str = table.column("column_lineage")[0].as_py()
        assert col_lineage_str is not None
        stored = json.loads(col_lineage_str)
        assert len(stored) == 2
        assert stored[0]["source_dataset"] == "src_ds"
        assert stored[0]["transform_expr"] == "UPPER(col_a)"

    def test_record_event_without_column_lineage(self, tmp_path: Path) -> None:
        store = LineageStore(LanceStorageManager(str(tmp_path)))
        event = create_lineage_event("ds", "create")

        store.record_event(event)

        table = store._storage.read_dataset("_lineage_events")
        col_lineage_str = table.column("column_lineage")[0].as_py()
        assert col_lineage_str is None

    def test_backward_compatible_old_schema(self, tmp_path: Path) -> None:
        """Old events without column_lineage field still readable."""
        store = LineageStore(LanceStorageManager(str(tmp_path)))
        event = create_lineage_event("ds", "create")
        store.record_event(event)

        history = store.get_dataset_history("ds")
        assert len(history) == 1
        assert history[0].dataset_name == "ds"


# ---------------------------------------------------------------------------
# Lineage hooks — fire and forget
# ---------------------------------------------------------------------------


class TestAutoRecordIngest:
    """Test auto_record_ingest hook."""

    def test_records_event(self, tmp_path: Path) -> None:
        storage = LanceStorageManager(str(tmp_path))
        auto_record_ingest(
            storage, "target_ds",
            source_files=["/data/file1.csv", "/data/file2.parquet"],
        )

        store = LineageStore(storage)
        history = store.get_dataset_history("target_ds")
        assert len(history) == 1
        assert history[0].operation == "append"
        assert history[0].transform_type == "ingest"
        assert history[0].actor == "system:ingest-pipeline"

    def test_no_source_files(self, tmp_path: Path) -> None:
        storage = LanceStorageManager(str(tmp_path))
        auto_record_ingest(storage, "ds", source_files=None)
        store = LineageStore(storage)
        history = store.get_dataset_history("ds")
        assert len(history) == 1

    def test_failure_does_not_raise(self) -> None:
        """Hook failure never blocks caller."""
        bad_storage = MagicMock()
        bad_storage.dataset_exists.side_effect = RuntimeError("disk full")
        # Should not raise
        auto_record_ingest(bad_storage, "ds")


class TestAutoRecordQuery:
    """Test auto_record_query hook."""

    def test_records_event(self, tmp_path: Path) -> None:
        storage = LanceStorageManager(str(tmp_path))
        auto_record_query(storage, "ds", "SELECT * FROM t WHERE x > 5", result_rows=42)

        store = LineageStore(storage)
        history = store.get_dataset_history("ds")
        assert len(history) == 1
        assert history[0].operation == "query"
        assert history[0].transform_type == "olap-query"


class TestAutoRecordRAG:
    """Test auto_record_rag hook."""

    def test_records_event_with_chunks(self, tmp_path: Path) -> None:
        storage = LanceStorageManager(str(tmp_path))
        chunks = [
            {"dataset": "kb_docs", "text": "chunk1"},
            {"dataset": "kb_docs", "text": "chunk2"},
            {"dataset_name": "kb_faq", "text": "chunk3"},
        ]
        auto_record_rag(storage, "rag_result", "what is X?", retrieved_chunks=chunks)

        store = LineageStore(storage)
        history = store.get_dataset_history("rag_result")
        assert len(history) == 1
        assert history[0].operation == "rag-query"
        # Should deduplicate source datasets
        assert "kb_docs" in history[0].source_datasets
        assert "kb_faq" in history[0].source_datasets


class TestAutoRecordExport:
    """Test auto_record_export hook."""

    def test_records_event(self, tmp_path: Path) -> None:
        storage = LanceStorageManager(str(tmp_path))
        auto_record_export(storage, "ds", "/output/data.parquet", fmt="parquet")

        store = LineageStore(storage)
        history = store.get_dataset_history("ds")
        assert len(history) == 1
        assert history[0].transform_type == "file-export"


class TestAutoRecordFederated:
    """Test auto_record_federated hook."""

    def test_records_event(self, tmp_path: Path) -> None:
        storage = LanceStorageManager(str(tmp_path))
        auto_record_federated(
            storage,
            catalog_tables=[
                ("catalog1.schema.table_a", "ta"),
                ("catalog2.schema.table_b", "tb"),
            ],
            join_sql="SELECT * FROM ta JOIN tb ON ta.id = tb.id",
        )

        store = LineageStore(storage)
        history = store.get_dataset_history("_federated_result")
        assert len(history) == 1
        assert history[0].transform_type == "cross-catalog-sql"
        assert "catalog1.schema.table_a" in history[0].source_datasets


# ---------------------------------------------------------------------------
# _extract_source_datasets helper
# ---------------------------------------------------------------------------


class TestExtractSourceDatasets:
    """Test source file path to dataset name conversion."""

    def test_csv_file(self) -> None:
        result = _extract_source_datasets(["/data/sales_2024.csv"])
        assert result == ["file:sales_2024"]

    def test_parquet_file(self) -> None:
        result = _extract_source_datasets(["/data/events.parquet"])
        assert result == ["file:events"]

    def test_json_file(self) -> None:
        result = _extract_source_datasets(["users.jsonl"])
        assert result == ["file:users"]

    def test_none(self) -> None:
        assert _extract_source_datasets(None) == []

    def test_empty(self) -> None:
        assert _extract_source_datasets([]) == []

    def test_mixed_files(self) -> None:
        result = _extract_source_datasets(["/a/file1.csv", "/b/file2.parquet"])
        assert len(result) == 2
        assert "file:file1" in result
        assert "file:file2" in result


# ---------------------------------------------------------------------------
# Mermaid / DOT visualization helpers
# ---------------------------------------------------------------------------


class TestMermaidOutput:
    """Test Mermaid diagram generation."""

    def test_basic_graph(self) -> None:
        from arrow_lake.api.routers.lineage import _to_mermaid

        graph = {
            "nodes": [
                {"id": "source_ds", "depth": 0, "type": "source"},
                {"id": "target_ds", "depth": 1, "type": "target"},
            ],
            "edges": [
                {"from": "source_ds", "to": "target_ds", "operation": "transform"},
            ],
        }
        result = _to_mermaid("target_ds", graph)
        text = result.body.decode() if isinstance(result.body, bytes) else result.body

        assert "graph LR" in text
        assert "source_ds -->|transform| target_ds" in text
        assert result.media_type == "text/x-mermaid"

    def test_empty_graph(self) -> None:
        from arrow_lake.api.routers.lineage import _to_mermaid

        result = _to_mermaid("ds", {"nodes": [], "edges": []})
        text = result.body.decode() if isinstance(result.body, bytes) else result.body
        assert "graph LR" in text


class TestDotOutput:
    """Test Graphviz DOT diagram generation."""

    def test_basic_graph(self) -> None:
        from arrow_lake.api.routers.lineage import _to_dot

        graph = {
            "nodes": [
                {"id": "src", "depth": 0, "type": "source"},
                {"id": "dst", "depth": 1, "type": "derived"},
            ],
            "edges": [
                {"from": "src", "to": "dst", "operation": "append"},
            ],
        }
        result = _to_dot("dst", graph)
        text = result.body.decode() if isinstance(result.body, bytes) else result.body

        assert "digraph lineage" in text
        assert 'label="Lineage: dst"' in text
        assert "src -> dst" in text
        assert 'label="append"' in text
        assert result.media_type == "text/vnd.graphviz"

    def test_node_colors(self) -> None:
        from arrow_lake.api.routers.lineage import _to_dot

        graph = {
            "nodes": [
                {"id": "src", "depth": 0, "type": "source"},
                {"id": "target", "depth": 0, "type": "target"},
                {"id": "derived", "depth": 1, "type": "derived"},
            ],
            "edges": [],
        }
        result = _to_dot("target", graph)
        text = result.body.decode() if isinstance(result.body, bytes) else result.body

        # source = blue, target = green, derived = orange
        assert "#2196F3" in text  # source
        assert "#4CAF50" in text  # target
        assert "#FF9800" in text  # derived
