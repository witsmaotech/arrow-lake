"""Import/export mixin for HugeGraphClient.

Contains export_graph and import_graph methods.

The mixin assumes the host class provides:
- self._config: HugeGraphConfig (with graph_name attribute)
- self._graph_base: str — base path like '/graphs/{name}'
- self.gremlin(query): async Gremlin query execution
- self._get(path, *, params): async GET returning httpx.Response
- self.add_vertices(vertices): async batch vertex insert
- self.add_edges(edges): async batch edge insert
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class _ImportExportMixin:
    """Graph import/export methods for HugeGraph REST client."""

    async def export_graph(self, *, with_properties: bool = True) -> dict[str, Any]:
        """Export full graph as JSON dict: {vertices: [...], edges: [...]}.

        Tries Gremlin first; falls back to REST API when the Gremlin
        script engine has no graph bindings (HugeGraph 1.7 all-in-one).
        """
        vertices: list[Any] = []
        edges: list[Any] = []

        # ── Try Gremlin path first ────────────────────────────────────
        try:
            if with_properties:
                v_result = await self.gremlin(
                    f"{self._config.graph_name}.traversal().V().valueMap().toList()"
                )
                vertices = v_result if isinstance(v_result, list) else []
            else:
                v_result = await self.gremlin(
                    f"{self._config.graph_name}.traversal().V().id().toList()"
                )
                vertices = [
                    {"id": v} for v in (v_result if isinstance(v_result, list) else [])
                ]

            e_result = await self.gremlin(
                f"{self._config.graph_name}.traversal().E().valueMap("
                '"label","outV","inV","outVLabel","inVLabel","properties").toList()'
            )
            edges = e_result if isinstance(e_result, list) else []

            return {"vertices": vertices, "edges": edges}

        except Exception as exc:
            logger.info(
                "Gremlin export failed (%s), falling back to REST API", exc
            )

        # ── REST API fallback ─────────────────────────────────────────
        # HugeGraph REST: GET /graphs/{name}/graph/vertices?limit=-1
        #                 GET /graphs/{name}/graph/edges?limit=-1
        base = self._graph_base

        v_resp = await self._get(f"{base}/graph/vertices", params={"limit": -1})
        if v_resp.status_code == 200:
            body = v_resp.json()
            v_list = body.get("vertices", [])
            if with_properties:
                vertices = v_list
            else:
                vertices = [{"id": v.get("id")} for v in v_list]

        e_resp = await self._get(f"{base}/graph/edges", params={"limit": -1})
        if e_resp.status_code == 200:
            body = e_resp.json()
            edges = body.get("edges", [])

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
