"""Targeted tests for catalog/lineage.py — uncovered paths."""

from __future__ import annotations

import pytest

from arrow_lake.catalog.lineage import (
    ColumnMapping,
    LineageEvent,
    LineageQueryBridge,
    create_lineage_event,
)
from arrow_lake.exceptions import CatalogError


class TestLineageEvent:
    def test_from_dict(self) -> None:
        data = {
            "event_id": "e1",
            "timestamp": "2024-01-01T00:00:00",
            "dataset_name": "ds",
            "operation": "create",
            "source_datasets": [],
            "transform_type": "",
            "lance_version": None,
            "actor": "system",
            "metadata": [],
        }
        event = LineageEvent.from_dict(data)
        assert event.event_id == "e1"
        assert event.dataset_name == "ds"

    def test_from_dict_with_sources(self) -> None:
        data = {
            "event_id": "e2",
            "timestamp": "2024-01-01T00:00:00",
            "dataset_name": "target",
            "operation": "transform",
            "source_datasets": ["src1", "src2"],
            "transform_type": "etl",
            "lance_version": 5,
            "actor": "user",
            "metadata": [("key", "val")],
        }
        event = LineageEvent.from_dict(data)
        assert event.source_datasets == ("src1", "src2")
        assert event.lance_version == 5


class TestColumnMapping:
    def test_creation(self) -> None:
        m = ColumnMapping(source_dataset="src", source_column="col", target_column="tgt")
        assert m.source_dataset == "src"


class TestCreateLineageEvent:
    def test_basic_event(self) -> None:
        event = create_lineage_event("ds", "create")
        assert event.dataset_name == "ds"
        assert event.operation == "create"
        assert event.actor == "system"
        assert event.event_id  # non-empty

    def test_with_sources_and_metadata(self) -> None:
        event = create_lineage_event(
            "ds", "transform",
            source_datasets=["src1"],
            transform_type="etl",
            lance_version=3,
            actor="user",
            metadata={"key": "val"},
        )
        assert event.source_datasets == ("src1",)
        assert event.transform_type == "etl"
        assert event.lance_version == 3
        assert event.metadata == (("key", "val"),)


class TestLineageQueryBridgeValidateSql:
    def test_empty_sql_rejected(self) -> None:
        with pytest.raises(CatalogError, match="must not be empty"):
            LineageQueryBridge._validate_sql("")

    def test_non_select_rejected(self) -> None:
        with pytest.raises(CatalogError, match="Only SELECT"):
            LineageQueryBridge._validate_sql("DROP TABLE t")

    def test_dangerous_keyword_rejected(self) -> None:
        with pytest.raises(CatalogError, match="not allowed"):
            LineageQueryBridge._validate_sql("SELECT * FROM t; DELETE FROM t")

    def test_semicolon_rejected(self) -> None:
        with pytest.raises(CatalogError, match="Semicolons"):
            LineageQueryBridge._validate_sql("SELECT * FROM t;")

    def test_valid_select_passes(self) -> None:
        LineageQueryBridge._validate_sql("SELECT * FROM lineage_events")

    def test_select_with_comments(self) -> None:
        LineageQueryBridge._validate_sql("SELECT * -- comment\nFROM t")
