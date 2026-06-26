"""Tests for OlapSearchBridge.graph_query() — recursive CTE graph traversal.

PGQ (CREATE PROPERTY GRAPH / MATCH) is unavailable in the bundled DuckDB build,
so graph_query uses a cycle-safe recursive CTE. These tests run a REAL DuckDB
session (pyarrow_fallback registration) to validate the traversal semantics.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pytest
from arrow_lake.config import OlapConfig


class _FakeStorage:
    """Fake storage returning a fixed edges Arrow table from read_dataset."""

    def __init__(self, table: pa.Table) -> None:
        self._table = table

    def read_dataset(self, name: str) -> pa.Table:
        return self._table


def _bridge(table: pa.Table, **cfg: Any) -> Any:
    from arrow_lake.query.olap import OlapSearchBridge

    config = OlapConfig(lance_scan_mode="pyarrow_fallback", **cfg)
    return OlapSearchBridge(_FakeStorage(table), config=config)


class TestGraphQuery:
    """Recursive-CTE graph traversal over an edges dataset."""

    def test_directed_traversal_reaches_all_downstream(self) -> None:
        edges = pa.table(
            {"src": [1, 2, 3, 1], "dst": [2, 3, 4, 5], "w": [0.5, 0.9, 0.1, 0.7]}
        )
        res = _bridge(edges).graph_query(
            "edges", start_node=1, max_depth=5, weight_col="w"
        )
        rows = {r["node"]: r["depth"] for r in res.table.to_pylist()}
        assert rows == {"1": 0, "2": 1, "5": 1, "3": 2, "4": 3}
        # start node is row 0
        assert res.row_count >= 1
        assert res.table.to_pylist()[0]["node"] == "1"

    def test_directed_does_not_reach_upstream(self) -> None:
        edges = pa.table({"src": [1, 2], "dst": [2, 3]})
        res = _bridge(edges).graph_query("edges", start_node=3, max_depth=5)
        nodes = {r["node"] for r in res.table.to_pylist()}
        assert nodes == {"3"}  # 3 has no outgoing edges

    def test_undirected_traverses_both_directions(self) -> None:
        edges = pa.table({"src": [1, 2], "dst": [2, 3]})
        res = _bridge(edges).graph_query(
            "edges", start_node=3, max_depth=5, directed=False
        )
        nodes = {r["node"] for r in res.table.to_pylist()}
        assert nodes == {"3", "2", "1"}

    def test_cycle_safe_bounded(self) -> None:
        # 1 <-> 2 cycle: must not loop and each node appears once (cycle guard)
        edges = pa.table({"src": [1, 2], "dst": [2, 1]})
        res = _bridge(edges).graph_query("edges", start_node=1, max_depth=3)
        rows = {r["node"]: r["depth"] for r in res.table.to_pylist()}
        assert rows == {"1": 0, "2": 1}

    def test_cost_sums_along_path(self) -> None:
        edges = pa.table(
            {"src": [1, 2, 3], "dst": [2, 3, 4], "w": [0.5, 0.9, 0.1]}
        )
        res = _bridge(edges).graph_query(
            "edges", start_node=1, max_depth=5, weight_col="w"
        )
        by_node = {r["node"]: r["cost"] for r in res.table.to_pylist()}
        assert float(by_node["4"]) == pytest.approx(1.5)  # 0.5 + 0.9 + 0.1
        assert float(by_node["2"]) == pytest.approx(0.5)

    def test_no_weight_col_omits_cost(self) -> None:
        edges = pa.table({"src": [1], "dst": [2]})
        res = _bridge(edges).graph_query("edges", start_node=1, max_depth=2)
        assert "cost" not in res.table.column_names
        assert set(res.table.column_names) == {"depth", "node", "path"}

    def test_string_node_ids(self) -> None:
        edges = pa.table(
            {"src": ["a", "b"], "dst": ["b", "c"], "w": [1.0, 2.0]}
        )
        res = _bridge(edges).graph_query("edges", start_node="a", max_depth=5)
        nodes = {r["node"] for r in res.table.to_pylist()}
        assert nodes == {"a", "b", "c"}

    def test_max_depth_bounds_reach(self) -> None:
        # Linear chain 1->2->3->4->5; max_depth=2 reaches only up to node 3
        edges = pa.table({"src": [1, 2, 3, 4], "dst": [2, 3, 4, 5]})
        res = _bridge(edges).graph_query("edges", start_node=1, max_depth=2)
        rows = {r["node"]: r["depth"] for r in res.table.to_pylist()}
        assert rows == {"1": 0, "2": 1, "3": 2}

    def test_max_depth_clamped(self, caplog: pytest.LogCaptureFixture) -> None:
        edges = pa.table({"src": [1], "dst": [2]})
        with caplog.at_level("WARNING"):
            _bridge(edges).graph_query("edges", start_node=1, max_depth=99)
        assert "clamped" in caplog.text

    def test_invalid_column_raises(self) -> None:
        edges = pa.table({"src": [1], "dst": [2]})
        with pytest.raises(ValueError):
            _bridge(edges).graph_query("edges", start_node=1, src_col="bad col!")

    def test_invalid_dataset_name_raises(self) -> None:
        edges = pa.table({"src": [1], "dst": [2]})
        with pytest.raises(ValueError):
            _bridge(edges).graph_query("../etc", start_node=1)
