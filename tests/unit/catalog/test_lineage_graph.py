"""Tests for lineage graph tracing, impact analysis, and new API models."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.catalog.lineage import LineageEvent, LineageQueryBridge


def _make_event(
    dataset_name: str,
    operation: str = "transform",
    source_datasets: tuple[str, ...] = (),
    transform_type: str = "",
) -> LineageEvent:
    return LineageEvent(
        event_id="test-id",
        timestamp="2026-01-01T00:00:00",
        dataset_name=dataset_name,
        operation=operation,
        source_datasets=source_datasets,
        transform_type=transform_type,
        lance_version=None,
        actor="test",
        metadata=(),
    )


class TestTraceFullGraph:
    """Test LineageQueryBridge.trace_full_graph."""

    def test_empty_graph_for_unknown_dataset(self) -> None:
        store = MagicMock()
        bridge = LineageQueryBridge(store)
        # trace_upstream and trace_downstream return empty lists
        bridge.trace_upstream = MagicMock(return_value=[])
        bridge.trace_downstream = MagicMock(return_value=[])

        result = bridge.trace_full_graph("unknown")
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["id"] == "unknown"
        assert result["nodes"][0]["type"] == "target"
        assert result["edges"] == []
        assert result["stats"]["total_nodes"] == 1
        assert result["stats"]["total_edges"] == 0

    def test_linear_chain_graph(self) -> None:
        store = MagicMock()
        bridge = LineageQueryBridge(store)

        # A → B → C
        bridge.trace_upstream = MagicMock(
            side_effect=lambda name: {
                "A": [],
                "B": [_make_event("B", source_datasets=("A",))],
                "C": [_make_event("C", source_datasets=("B",))],
            }.get(name, [])
        )
        bridge.trace_downstream = MagicMock(
            side_effect=lambda name: {
                "A": [_make_event("B", source_datasets=("A",))],
                "B": [_make_event("C", source_datasets=("B",))],
                "C": [],
            }.get(name, [])
        )

        result = bridge.trace_full_graph("B")
        node_ids = {n["id"] for n in result["nodes"]}
        assert "A" in node_ids
        assert "B" in node_ids
        assert "C" in node_ids
        assert result["stats"]["total_nodes"] == 3

    def test_max_depth_limits_traversal(self) -> None:
        store = MagicMock()
        bridge = LineageQueryBridge(store)

        bridge.trace_upstream = MagicMock(return_value=[])
        bridge.trace_downstream = MagicMock(
            side_effect=lambda name: (
                [_make_event(f"{name}_child", source_datasets=(name,))]
                if not name.endswith("_child")
                else []
            )
        )

        result = bridge.trace_full_graph("root", max_depth=2)
        # root -> root_child -> root_child_child (depth 2)
        # root_child_child_child should NOT appear
        assert result["stats"]["max_depth"] <= 2

    def test_max_nodes_truncates_large_graph(self) -> None:
        store = MagicMock()
        bridge = LineageQueryBridge(store)

        # Infinite chain: every node has exactly one downstream child.
        bridge.trace_upstream = MagicMock(return_value=[])
        bridge.trace_downstream = MagicMock(
            side_effect=lambda name: [_make_event(f"{name}_c", source_datasets=(name,))]
        )

        result = bridge.trace_full_graph("root", max_nodes=3)
        # BFS stops once 3 nodes are reached → truncated flag set, no more.
        assert result["stats"]["truncated"] is True
        assert result["stats"]["total_nodes"] <= 3

        # Default max_nodes is generous → same small graph is NOT truncated.
        result_default = bridge.trace_full_graph("root", max_depth=2)
        assert result_default["stats"].get("truncated", False) is False


class TestTraceImpact:
    """Test LineageQueryBridge.trace_impact."""

    def test_no_downstream_impact(self) -> None:
        store = MagicMock()
        bridge = LineageQueryBridge(store)
        bridge.trace_downstream = MagicMock(return_value=[])

        result = bridge.trace_impact("isolated_dataset")
        assert result == []

    def test_cascading_impact(self) -> None:
        store = MagicMock()
        bridge = LineageQueryBridge(store)

        # A → B, A → C → D
        bridge.trace_downstream = MagicMock(
            side_effect=lambda name: {
                "A": [
                    _make_event("B", source_datasets=("A",)),
                    _make_event("C", source_datasets=("A",)),
                ],
                "B": [],
                "C": [_make_event("D", source_datasets=("C",))],
                "D": [],
            }.get(name, [])
        )

        result = bridge.trace_impact("A")
        impacted_names = {item["dataset"] for item in result}
        assert "B" in impacted_names
        assert "C" in impacted_names
        assert "D" in impacted_names
        # Verify depth ordering
        depths = {item["dataset"]: item["depth"] for item in result}
        assert depths["B"] == 1
        assert depths["C"] == 1
        assert depths["D"] == 2

    def test_circular_dependency_handled(self) -> None:
        store = MagicMock()
        bridge = LineageQueryBridge(store)

        # A → B → A (circular)
        bridge.trace_downstream = MagicMock(
            side_effect=lambda name: {
                "A": [_make_event("B", source_datasets=("A",))],
                "B": [_make_event("A", source_datasets=("B",))],
            }.get(name, [])
        )

        result = bridge.trace_impact("A")
        # Should not infinite loop
        assert len(result) >= 1


class TestLineageAPIModels:
    """Test new lineage API models."""

    def test_lineage_node(self) -> None:
        from arrow_lake.api.models.lineage import LineageNode

        node = LineageNode(id="test_ds", depth=2, type="derived")
        assert node.id == "test_ds"
        assert node.depth == 2
        assert node.type == "derived"

    def test_lineage_edge_with_from_alias(self) -> None:
        from arrow_lake.api.models.lineage import LineageEdge

        edge = LineageEdge(**{"from": "A", "to": "B", "operation": "transform"})
        assert edge.from_ == "A"
        assert edge.to == "B"

    def test_lineage_graph_response(self) -> None:
        from arrow_lake.api.models.lineage import (
            LineageGraphResponse,
            LineageGraphStats,
            LineageNode,
        )

        resp = LineageGraphResponse(
            dataset_name="test",
            nodes=[LineageNode(id="a")],
            stats=LineageGraphStats(total_nodes=1),
        )
        assert resp.success is True
        assert resp.stats.total_nodes == 1

    def test_lineage_impact_response(self) -> None:
        from arrow_lake.api.models.lineage import (
            LineageImpactItem,
            LineageImpactResponse,
        )

        resp = LineageImpactResponse(
            source_dataset="A",
            impacted_datasets=[
                LineageImpactItem(dataset="B", depth=1, operation="transform"),
            ],
        )
        assert resp.success is True
        assert len(resp.impacted_datasets) == 1

    def test_lineage_stats_response(self) -> None:
        from arrow_lake.api.models.lineage import LineageStatsResponse

        resp = LineageStatsResponse(
            total_datasets_tracked=5, total_events=42
        )
        assert resp.total_datasets_tracked == 5
        assert resp.total_events == 42
