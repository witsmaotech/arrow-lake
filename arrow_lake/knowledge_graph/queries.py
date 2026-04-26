"""Pre-defined Gremlin query templates for HugeGraph.

All queries use the ``{graph_name}.traversal()`` traversal source format
required by HugeGraph 1.7.0 (NOT ``g.V()``).
"""

from __future__ import annotations

from typing import Any


def _gremlin_escape(s: str) -> str:
    """Escape special characters for safe embedding in Gremlin string literals."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


class GremlinQueries:
    """Pre-defined Gremlin query templates."""

    @staticmethod
    def find_entity(
        name: str,
        entity_type: str = "",
        graph_name: str = "hugegraph",
    ) -> str:
        """Find entity vertex by name, optionally filtered by label.

        Uses ``eq()`` for exact match (NOT textContains).
        """
        escaped_name = _gremlin_escape(name)
        parts = [f'{graph_name}.traversal().V()']
        if entity_type:
            parts.append(f'hasLabel("{_gremlin_escape(entity_type)}")')
        parts.append(f'has("name",eq("{escaped_name}"))')
        return ".".join(parts)

    @staticmethod
    def get_neighbors(
        vertex_id: str,
        depth: int = 2,
        graph_name: str = "hugegraph",
    ) -> str:
        """Multi-hop neighbor traversal (outgoing edges only).

        Uses ``repeat(out()).simplePath().times(depth)``.
        """
        return (
            f'{graph_name}.traversal().V("{_gremlin_escape(vertex_id)}")'
            f".repeat(out()).simplePath().times({depth})"
        )

    @staticmethod
    def shortest_path(
        source_id: str,
        target_id: str,
        graph_name: str = "hugegraph",
    ) -> str:
        """Shortest path between two vertices via outgoing edges."""
        return (
            f'{graph_name}.traversal().V("{_gremlin_escape(source_id)}")'
            f'.repeat(out()).until(__.is("{_gremlin_escape(target_id)}")).path()'
        )

    @staticmethod
    def get_subgraph(
        center_id: str,
        radius: int = 2,
        graph_name: str = "hugegraph",
    ) -> str:
        """Get subgraph around a center vertex (both directions).

        Uses ``repeat(both()).simplePath().times(radius)``.
        """
        return (
            f'{graph_name}.traversal().V("{_gremlin_escape(center_id)}")'
            f".repeat(both()).simplePath().times({radius})"
        )

    @staticmethod
    def entity_type_counts(graph_name: str = "hugegraph") -> str:
        """Count vertices grouped by label."""
        return f"{graph_name}.traversal().V().groupCount().by(label)"

    @staticmethod
    def traverse_from_entities(
        entity_names: list[str],
        depth: int = 2,
        graph_name: str = "hugegraph",
    ) -> str:
        """Multi-entity traversal for GraphRAG.

        Finds vertices by name, then expands neighbors via outgoing edges.
        Uses ``union()`` to combine multiple entity lookups.
        """
        if len(entity_names) == 0:
            return ""
        escaped = [_gremlin_escape(n) for n in entity_names]
        union_parts = [f'V().has("name",eq("{n}"))' for n in escaped]
        entity_pattern = "union(" + ",".join(union_parts) + ")"

        return (
            f"{graph_name}.traversal().{entity_pattern}"
            f".repeat(out()).simplePath().times({depth})"
        )

    # ------------------------------------------------------------------
    # Traverser Templates (REST API equivalents as Gremlin)
    # ------------------------------------------------------------------

    @staticmethod
    def all_shortest_paths(
        source_id: str,
        target_id: str,
        graph_name: str = "hugegraph",
    ) -> str:
        """All shortest paths between two vertices."""
        return (
            f'{graph_name}.traversal().V("{_gremlin_escape(source_id)}")'
            f'.repeat(out().simplePath()).until(__.is("{_gremlin_escape(target_id)}"))'
            f".path()"
        )

    @staticmethod
    def weighted_shortest_path(
        source_id: str,
        target_id: str,
        weight_prop: str = "weight",
        graph_name: str = "hugegraph",
    ) -> str:
        """Weighted shortest path (Gremlin approximation)."""
        return (
            f'{graph_name}.traversal().V("{_gremlin_escape(source_id)}")'
            f'.repeat(outE().hasLabel("{_gremlin_escape(weight_prop)}").inV().simplePath())'
            f'.until(__.is("{_gremlin_escape(target_id)}")).path().by("weight")'
        )

    @staticmethod
    def single_source_shortest_path(
        source_id: str,
        graph_name: str = "hugegraph",
    ) -> str:
        """Single source shortest path to all reachable vertices."""
        return (
            f'{graph_name}.traversal().V("{_gremlin_escape(source_id)}")'
            f".repeat(out().simplePath()).emit().path()"
        )

    @staticmethod
    def multi_node_shortest_path(
        source_ids: list[str],
        target_ids: list[str],
        graph_name: str = "hugegraph",
    ) -> str:
        """Multi node shortest paths (Gremlin approximation)."""
        s_ids = ",".join(f'"{_gremlin_escape(s)}"' for s in source_ids)
        t_ids = ",".join(f'"{_gremlin_escape(t)}"' for t in target_ids)
        return (
            f"{graph_name}.traversal().V({s_ids})"
            f".repeat(out().simplePath()).emit(hasId({t_ids})).path()"
        )

    @staticmethod
    def rays(
        source_id: str,
        max_depth: int = 5,
        graph_name: str = "hugegraph",
    ) -> str:
        """Rays — non-cyclic paths from source (no return to start)."""
        return (
            f'{graph_name}.traversal().V("{_gremlin_escape(source_id)}")'
            f".repeat(out().simplePath())"
            f".until(__.loops().is(gt({max_depth - 1})))"
            f".path()"
        )

    @staticmethod
    def rings(
        source_id: str,
        max_depth: int = 5,
        graph_name: str = "hugegraph",
    ) -> str:
        """Ring detection — paths from source back to itself."""
        return (
            f'{graph_name}.traversal().V("{_gremlin_escape(source_id)}")'
            f".repeat(out().simplePath())"
            f'.until(__.is("{_gremlin_escape(source_id)}").and().loops().is(gt(0)))'
            f".path()"
        )

    @staticmethod
    def crosspoints(
        source_id: str,
        target_id: str,
        graph_name: str = "hugegraph",
    ) -> str:
        """Crosspoints — vertices on paths between source and target."""
        return (
            f'{graph_name}.traversal().V("{_gremlin_escape(source_id)}")'
            f'.repeat(out().simplePath()).until(__.is("{_gremlin_escape(target_id)}"))'
            f".path()"
        )

    @staticmethod
    def customized_paths(
        source_id: str,
        steps: list[dict[str, Any]],
        graph_name: str = "hugegraph",
    ) -> str:
        """Customized multi-step path traversal as Gremlin.

        Each step dict: {direction: str, labels: list[str]}.
        """
        parts = [
            f'{graph_name}.traversal().V("{_gremlin_escape(source_id)}")'
        ]
        for step in steps:
            direction = step.get("direction", "OUT")
            labels = step.get("labels", [])
            if direction == "IN":
                parts.append("in()")
            elif direction == "BOTH":
                parts.append("both()")
            else:
                parts.append("out()")
            if labels:
                label_str = ",".join(f'"{_gremlin_escape(lbl)}"' for lbl in labels)
                parts.append(f"hasLabel({label_str})")
        parts.append("path()")
        return ".".join(parts)
