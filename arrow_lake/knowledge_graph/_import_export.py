"""Import/export mixin for HugeGraphClient.

Contains export_graph and import_graph methods.

The mixin assumes the host class provides:
- self._config: HugeGraphConfig (with graph_name attribute)
- self.gremlin(query): async Gremlin query execution
- self.add_vertices(vertices): async batch vertex insert
- self.add_edges(edges): async batch edge insert
"""

from __future__ import annotations

from typing import Any


class _ImportExportMixin:
    """Graph import/export methods for HugeGraph REST client."""

    async def export_graph(self, *, with_properties: bool = True) -> dict[str, Any]:
        """Export full graph as JSON dict: {vertices: [...], edges: [...]}."""
        vertices = []
        edges = []

        if with_properties:
            v_result = await self.gremlin(
                f"{self._config.graph_name}.traversal().V().valueMap().toList()"
            )
            vertices = v_result if isinstance(v_result, list) else []
        else:
            v_result = await self.gremlin(
                f"{self._config.graph_name}.traversal().V().id().toList()"
            )
            vertices = [{"id": v} for v in (v_result if isinstance(v_result, list) else [])]

        e_result = await self.gremlin(
            f"{self._config.graph_name}.traversal().E().valueMap("
            '"label","outV","inV","outVLabel","inVLabel","properties").toList()'
        )
        edges = e_result if isinstance(e_result, list) else []

        return {"vertices": vertices, "edges": edges}

    async def import_graph(self, data: dict[str, Any]) -> dict[str, Any]:
        """Import graph from JSON dict. Returns {vertices_added, edges_added}."""
        vertex_data = data.get("vertices", [])
        edge_data = data.get("edges", [])

        vertices_added = 0
        edges_added = 0

        if vertex_data:
            ids = await self.add_vertices(vertex_data)
            vertices_added = len(ids)

        if edge_data:
            edges_added = await self.add_edges(edge_data)

        return {"vertices_added": vertices_added, "edges_added": edges_added}
