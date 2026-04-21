"""Pre-defined Gremlin query templates for HugeGraph.

All queries use the ``{graph_name}.traversal()`` traversal source format
required by HugeGraph 1.7.0 (NOT ``g.V()``).
"""

from __future__ import annotations


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
