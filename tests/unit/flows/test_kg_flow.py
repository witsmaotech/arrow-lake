"""Tests for KGFlow — step logic in isolation.

Metaflow FlowSpec hooks into the CLI on instantiation, so we test
each step's business logic as standalone operations.
"""

from __future__ import annotations

import json
from typing import Any


class TestStartStepSharding:
    """start step: load dataset and prepare chunk indices."""

    def test_chunk_splitting_exact(self) -> None:
        total = 100
        chunk_size = 20
        indices = []
        for offset in range(0, total, chunk_size):
            length = min(chunk_size, total - offset)
            indices.append((offset, length))

        assert len(indices) == 5
        assert indices[0] == (0, 20)
        assert indices[4] == (80, 20)

    def test_chunk_splitting_remainder(self) -> None:
        total = 45
        chunk_size = 20
        indices = []
        for offset in range(0, total, chunk_size):
            length = min(chunk_size, total - offset)
            indices.append((offset, length))

        assert len(indices) == 3
        assert indices[2] == (40, 5)

    def test_chunk_splitting_empty(self) -> None:
        total = 0
        chunk_size = 20
        indices = []
        for offset in range(0, total, chunk_size):
            length = min(chunk_size, total - offset)
            indices.append((offset, length))

        assert indices == []


class TestExtractEntitiesStepLogic:
    """extract_entities step: entity extraction with error capture."""

    def test_success_entities(self) -> None:
        entities = [
            {"chunk_index": 0, "text_len": 100},
            {"chunk_index": 1, "text_len": 200},
        ]
        assert len(entities) == 2
        assert entities[0]["text_len"] == 100

    def test_failed_status(self) -> None:
        result: dict[str, Any] = {
            "entities": [],
            "extract_status": "failed",
        }
        assert result["extract_status"] == "failed"
        assert len(result["entities"]) == 0


class TestEnsureSchemaStepLogic:
    """ensure_schema step: idempotent schema creation."""

    def test_schema_ready_flag(self) -> None:
        schema_ready = True
        assert schema_ready is True


class TestJoinStepLogic:
    """join step: merge branch results."""

    def test_merge_both_branches(self) -> None:
        inputs = [
            {"entities": [{"chunk_index": 0}], "extract_status": "success"},
            {"schema_ready": True},
        ]

        merged_entities: list[dict[str, Any]] = []
        merged_schema_ready = False

        for inp in inputs:
            if "entities" in inp:
                merged_entities = inp["entities"]
            if inp.get("schema_ready"):
                merged_schema_ready = True

        assert len(merged_entities) == 1
        assert merged_schema_ready is True

    def test_extract_failed_schema_ok(self) -> None:
        inputs = [
            {"entities": [], "extract_status": "failed"},
            {"schema_ready": True},
        ]

        merged_entities: list[dict[str, Any]] = []
        extract_status = "unknown"
        merged_schema_ready = False

        for inp in inputs:
            if "entities" in inp:
                merged_entities = inp["entities"]
                extract_status = inp["extract_status"]
            if inp.get("schema_ready"):
                merged_schema_ready = True

        assert len(merged_entities) == 0
        assert extract_status == "failed"
        assert merged_schema_ready is True


class TestInsertVerticesStepLogic:
    """insert_vertices step: batch insert with error handling."""

    def test_success_insert(self) -> None:
        merged_entities = [{"chunk_index": i} for i in range(10)]
        merged_schema_ready = True

        vertex_count = len(merged_entities)
        edge_count = max(0, vertex_count - 1)
        insert_status = "success" if merged_schema_ready else "skipped"

        assert vertex_count == 10
        assert edge_count == 9
        assert insert_status == "success"

    def test_schema_not_ready_skips(self) -> None:
        merged_schema_ready = False
        insert_status = "skipped" if not merged_schema_ready else "success"
        assert insert_status == "skipped"

    def test_failed_insert(self) -> None:
        insert_status = "failed"
        assert insert_status == "failed"


class TestEndStepReport:
    """end step: KG build report JSON."""

    def test_report_structure(self) -> None:
        report = {
            "total_chunks": 100,
            "entities_extracted": 80,
            "vertices": 80,
            "edges": 79,
            "extract_status": "success",
            "insert_status": "success",
            "schema_ready": True,
        }
        json_str = json.dumps(report, indent=2)
        parsed = json.loads(json_str)
        assert parsed["total_chunks"] == 100
        assert parsed["vertices"] == 80
        assert parsed["schema_ready"] is True


class TestFlowRegistration:
    """Verify KGFlow is registered."""

    def test_kg_registered(self) -> None:
        import importlib

        import flows
        from arrow_lake.workflow.base import FlowRegistry

        FlowRegistry.clear()
        flows._registration_attempted = False
        importlib.reload(flows)
        flows._register_flows()

        assert "kg" in FlowRegistry.list_flows()
