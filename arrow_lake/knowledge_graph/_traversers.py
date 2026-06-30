"""Traverser API mixin for HugeGraphClient.

Contains all traverser methods: kneighbor, shortest_path, all_shortest_paths,
weighted_shortest_path, single_source_shortest_path, multi_node_shortest_path,
rays, rings, crosspoints, customized_paths.

The mixin assumes the host class provides:
- self._graph_base (str): e.g. "/graphs/{name}"
- self._config: HugeGraphConfig
- self._get(path, params): async GET with retry
- self._post(path, json_data): async POST with retry
- self._handle_http_error(exc): raises KGError from httpx errors
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from arrow_lake.exceptions import ErrorCode, KGError

logger = logging.getLogger(__name__)


class _TraverserMixin:
    """Traverser API methods for HugeGraph REST client."""

    async def traverser_kneighbor(
        self,
        source: str,
        depth: int | None = None,
        direction: str = "OUT",
        max_degree: int = 10000,
        limit: int = 100,
        graph_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """K-neighbor traversal via POST /traversers/kneighbor.

        Returns list of neighbor vertex dicts.
        """
        d = depth if depth is not None else self._config.default_traversal_depth
        body = {
            "source": source,
            "steps": {"direction": direction, "max_degree": max_degree},
            "max_depth": d,
            "with_vertex": True,
            "limit": limit,
        }
        try:
            resp = await self._post(
                f"{self._graph_base_for(graph_name)}/traversers/kneighbor", json_data=body
            )
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code != 200:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"K-neighbor traversal failed: {resp.text}",
                context={"source": source, "depth": d},
            )

        data = resp.json()
        # Response may have "vertices" key or "batch" key (async response)
        if "vertices" in data:
            return data["vertices"]
        if "batch" in data:
            return data["batch"]
        return []

    async def traverser_shortest_path(
        self,
        source: str,
        target: str,
        max_depth: int = 10,
        direction: str = "OUT",
        limit: int = 10,
        graph_name: str | None = None,
    ) -> dict[str, Any]:
        """All paths between source and target via POST /traversers/paths.

        Uses sources/targets (plural) with ids array format.
        """
        body = {
            "sources": {"ids": [source]},
            "targets": {"ids": [target]},
            "step": {"direction": direction, "max_degree": 10000},
            "max_depth": max_depth,
            "limit": limit,
        }
        try:
            resp = await self._post(
                f"{self._graph_base_for(graph_name)}/traversers/paths", json_data=body
            )
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code != 200:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Path traversal failed: {resp.text}",
                context={"source": source, "target": target},
            )

        return resp.json()

    async def traverser_all_shortest_paths(
        self,
        source: str,
        target: str,
        *,
        direction: str = "OUT",
        max_depth: int = 10,
        graph_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """All shortest paths between source and target.

        GET /graphs/{name}/traversers/allshortestpaths
        Returns list of path dicts with objects/labels/weights.
        """
        params = {
            "source": json.dumps(source),
            "target": json.dumps(target),
            "direction": direction,
            "max_depth": str(max_depth),
        }
        try:
            resp = await self._get(
                f"{self._graph_base_for(graph_name)}/traversers/allshortestpaths", params=params,
            )
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code != 200:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"All shortest paths traversal failed: {resp.text}",
                context={"source": source, "target": target},
            )

        data = resp.json()
        return data.get("paths", [])

    async def traverser_weighted_shortest_path(
        self,
        source: str,
        target: str,
        *,
        direction: str = "OUT",
        weight_prop: str = "weight",
        max_degree: int = 10000,
        graph_name: str | None = None,
    ) -> dict[str, Any]:
        """Weighted shortest path between source and target.

        GET /graphs/{name}/traversers/weightedshortestpath
        Returns dict with path and total weight.
        """
        params = {
            "source": json.dumps(source),
            "target": json.dumps(target),
            "direction": direction,
            "weight": weight_prop,
            "max_degree": str(max_degree),
        }
        try:
            resp = await self._get(
                f"{self._graph_base_for(graph_name)}/traversers/weightedshortestpath",
                params=params,
            )
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code != 200:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Weighted shortest path failed: {resp.text}",
                context={"source": source, "target": target},
            )

        return resp.json()

    async def traverser_single_source_shortest_path(
        self,
        source: str,
        *,
        direction: str = "OUT",
        weight_prop: str = "weight",
        max_degree: int = 10000,
        graph_name: str | None = None,
    ) -> dict[str, Any]:
        """Single source shortest path to all reachable vertices.

        GET /graphs/{name}/traversers/singlesourceshortestpath
        Returns dict mapping target_id to path info.
        """
        params = {
            "source": json.dumps(source),
            "direction": direction,
            "weight": weight_prop,
            "max_degree": str(max_degree),
        }
        try:
            resp = await self._get(
                f"{self._graph_base_for(graph_name)}/traversers/singlesourceshortestpath",
                params=params,
            )
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code != 200:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Single source shortest path failed: {resp.text}",
                context={"source": source},
            )

        return resp.json()

    async def traverser_multi_node_shortest_path(
        self,
        sources: list[str],
        targets: list[str],
        *,
        direction: str = "OUT",
        weight_prop: str = "weight",
        max_degree: int = 10000,
    ) -> list[dict[str, Any]]:
        """Shortest paths between multiple source-target pairs.

        NOTE: This endpoint does not exist in HugeGraph 1.7.0 REST API.
        """
        raise KGError(
            error_code=ErrorCode.KG_QUERY_FAILED,
            message=(
                "multi_node_shortest_path is not supported by HugeGraph 1.7.0. "
                "Use all_shortest_paths for single pair queries."
            ),
        )

    async def traverser_rays(
        self,
        source: str,
        *,
        direction: str = "OUT",
        max_depth: int = 5,
        graph_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Rays traversal -- non-cyclic paths from source.

        GET /graphs/{name}/traversers/rays
        Returns list of ray dicts with labels/objects.
        """
        params = {
            "source": json.dumps(source),
            "direction": direction,
            "max_depth": str(max_depth),
        }
        try:
            resp = await self._get(
                f"{self._graph_base_for(graph_name)}/traversers/rays", params=params,
            )
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code != 200:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Rays traversal failed: {resp.text}",
                context={"source": source},
            )

        data = resp.json()
        return data.get("rays", [])

    async def traverser_rings(
        self,
        source: str,
        *,
        direction: str = "OUT",
        max_depth: int = 5,
        graph_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Ring detection -- cyclic paths from source back to itself.

        GET /graphs/{name}/traversers/rings
        Returns list of ring dicts with labels/objects.
        """
        params = {
            "source": json.dumps(source),
            "direction": direction,
            "max_depth": str(max_depth),
        }
        try:
            resp = await self._get(
                f"{self._graph_base_for(graph_name)}/traversers/rings", params=params,
            )
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code != 200:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Rings traversal failed: {resp.text}",
                context={"source": source},
            )

        data = resp.json()
        return data.get("rings", [])

    async def traverser_crosspoints(
        self,
        source: str,
        target: str,
        *,
        direction: str = "OUT",
        max_depth: int = 5,
        graph_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Crosspoints -- vertices on paths between source and target.

        GET /graphs/{name}/traversers/crosspoints
        Returns list of crosspoint dicts with vertex and crossed_paths.
        """
        params = {
            "source": json.dumps(source),
            "target": json.dumps(target),
            "direction": direction,
            "max_depth": str(max_depth),
        }
        try:
            resp = await self._get(
                f"{self._graph_base_for(graph_name)}/traversers/crosspoints", params=params,
            )
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code != 200:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Crosspoints traversal failed: {resp.text}",
                context={"source": source, "target": target},
            )

        data = resp.json()
        return data.get("crosspoints", [])

    async def traverser_customized_paths(
        self,
        source: str,
        steps: list[dict[str, Any]],
        *,
        with_vertex: bool = True,
        with_edge: bool = True,
        graph_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Customized multi-step path traversal.

        POST /graphs/{name}/traversers/customizedpaths
        Each step dict: {direction, labels, max_degree, skip_degree}.
        """
        body: dict[str, Any] = {
            "sources": {"ids": [source]},
            "steps": steps,
            "with_vertex": with_vertex,
            "with_edge": with_edge,
        }
        try:
            resp = await self._post(
                f"{self._graph_base_for(graph_name)}/traversers/customizedpaths",
                json_data=body,
            )
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code != 200:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Customized paths traversal failed: {resp.text}",
            )

        data = resp.json()
        return data.get("paths", [])
