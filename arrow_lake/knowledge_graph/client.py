"""HugeGraph REST API client.

Async HTTP client for HugeGraph 1.7.0, using httpx + tenacity retry.
Does NOT depend on the hugegraph-client SDK.

Reference: memory/project_hugegraph_api_findings.md
Key API differences from local docs:
- Gremlin endpoint: POST /gremlin (NOT /graphs/{name}/gremlin)
- Traversal source: {graph_name}.traversal() (NOT g.V())
- Edge fields: outV/outVLabel/inV/inVLabel (NOT source/target)
- Schema creation returns 202 (async accepted)

HTTP method notes (verified against HugeGraph 1.7.0):
- POST: kneighbor, paths, customizedpaths (body params)
- GET:  allshortestpaths, weightedshortestpath, singlesourceshortestpath,
        rays, rings, crosspoints (JSON-encoded query params)
- N/A:  multinodesshortestpath (endpoint does not exist)
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from arrow_lake.config import HugeGraphConfig
from arrow_lake.exceptions import ErrorCode, KGError

logger = logging.getLogger(__name__)

# Safe vertex ID pattern: alphanumeric, CJK, underscore, hyphen, dot, space.
_SAFE_VERTEX_ID_RE = re.compile(r"^[a-zA-Z0-9_\-一-鿿　-〿＀-￯:.\s]+$")

_DEFAULT_MAX_RETRIES = 3

_BLOCKED_GREMLIN_PATTERNS = (
    "drop(", "eval(", "System.", "java.lang", "inject(",
    "GroovyShell", "ProcessBuilder", "Runtime.", "Exec(",
    "org.apache", "new File(", "Class.forName",
    "groovy.", "script(", "ExecTransformer",
    "安全管理器", "AccessController", "doPrivileged",
)


class HugeGraphClient:
    """HugeGraph REST API client (httpx async, no SDK dependency)."""

    def __init__(self, config: HugeGraphConfig) -> None:
        self._config = config
        self._base_url = f"http://{config.host}:{config.port}"
        self._graph_base = f"/graphs/{config.graph_name}"

        headers: dict[str, str] = {}
        if config.username and config.password:
            token = base64.b64encode(
                f"{config.username}:{config.password}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {token}"

        from arrow_lake.core.http import create_async_http_client

        self._client = create_async_http_client(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(config.timeout_seconds),
            http2=False,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        """GET with tenacity retry on transient failures."""
        return await self._client.get(path, params=params)

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _post(self, path: str, json_data: Any = None) -> httpx.Response:
        """POST with tenacity retry on transient failures."""
        return await self._client.post(path, json=json_data)

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _delete(self, path: str) -> httpx.Response:
        """DELETE with tenacity retry on transient failures."""
        return await self._client.delete(path)

    def _handle_http_error(self, exc: httpx.HTTPError) -> None:
        """Wrap httpx errors as KGError."""
        raise KGError(
            error_code=ErrorCode.KG_CONNECTION_FAILED,
            message=f"HTTP error calling HugeGraph at {self._base_url}: {exc}",
            context={"host": self._config.host, "port": self._config.port},
        ) from exc

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        """Check if HugeGraph server is reachable."""
        try:
            resp = await self._get("/versions")
            return resp.status_code == 200
        except (httpx.HTTPError, Exception):
            return False

    # ------------------------------------------------------------------
    # Gremlin queries
    # ------------------------------------------------------------------

    async def gremlin(self, query: str) -> list[Any]:
        """Execute a Gremlin query via POST /gremlin.

        The query must use {graph_name}.traversal() as the traversal source.
        Returns the data array from the result.
        """
        query_lower = query.lower()
        for pattern in _BLOCKED_GREMLIN_PATTERNS:
            if pattern.lower() in query_lower:
                raise KGError(
                    error_code=ErrorCode.KG_QUERY_ERROR,
                    message=f"Gremlin query contains blocked pattern: {pattern!r}",
                )

        try:
            resp = await self._post("/gremlin", json_data={"gremlin": query})
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        body = resp.json()
        status = body.get("status", {})
        if status.get("code", 0) != 200:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Gremlin query failed: {status.get('message', 'unknown')}",
                context={"query": query, "status_code": status.get("code")},
            )

        return body.get("result", {}).get("data", [])

    # ------------------------------------------------------------------
    # Vertex operations
    # ------------------------------------------------------------------

    async def add_vertices(self, vertices: list[dict[str, Any]]) -> list[str]:
        """Batch insert vertices. Returns list of created vertex IDs."""
        try:
            resp = await self._post(
                f"{self._graph_base}/graph/vertices/batch", json_data=vertices
            )
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code not in (200, 201):
            raise KGError(
                error_code=ErrorCode.KG_BUILD_FAILED,
                message=f"Batch vertex insert failed: {resp.text}",
                context={"status_code": resp.status_code},
            )

        ids = resp.json()
        return ids if isinstance(ids, list) else []

    async def get_vertex(self, vertex_id: str) -> dict[str, Any] | None:
        """Get a vertex by ID. Returns None if not found."""
        if not _SAFE_VERTEX_ID_RE.match(vertex_id):
            logger.warning("Rejected unsafe vertex_id: %r", vertex_id)
            return None
        try:
            resp = await self._get(
                f'{self._graph_base}/graph/vertices/"{vertex_id}"'
            )
        except httpx.HTTPError:
            return None

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            return None
        return resp.json()

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    async def add_edges(self, edges: list[dict[str, Any]]) -> int:
        """Batch insert edges. Returns count of inserted edges.

        Each edge must include: label, outV, outVLabel, inV, inVLabel, properties.
        """
        try:
            resp = await self._post(
                f"{self._graph_base}/graph/edges/batch", json_data=edges
            )
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code not in (200, 201):
            raise KGError(
                error_code=ErrorCode.KG_BUILD_FAILED,
                message=f"Batch edge insert failed: {resp.text}",
                context={"status_code": resp.status_code},
            )

        ids = resp.json()
        return len(ids) if isinstance(ids, list) else 0

    # ------------------------------------------------------------------
    # Traverser API
    # ------------------------------------------------------------------

    async def traverser_kneighbor(
        self,
        source: str,
        depth: int | None = None,
        direction: str = "OUT",
        max_degree: int = 10000,
        limit: int = 100,
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
                f"{self._graph_base}/traversers/kneighbor", json_data=body
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
                f"{self._graph_base}/traversers/paths", json_data=body
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
                f"{self._graph_base}/traversers/allshortestpaths", params=params,
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
                f"{self._graph_base}/traversers/weightedshortestpath",
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
                f"{self._graph_base}/traversers/singlesourceshortestpath",
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
    ) -> list[dict[str, Any]]:
        """Rays traversal — non-cyclic paths from source.

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
                f"{self._graph_base}/traversers/rays", params=params,
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
    ) -> list[dict[str, Any]]:
        """Ring detection — cyclic paths from source back to itself.

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
                f"{self._graph_base}/traversers/rings", params=params,
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
    ) -> list[dict[str, Any]]:
        """Crosspoints — vertices on paths between source and target.

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
                f"{self._graph_base}/traversers/crosspoints", params=params,
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
                f"{self._graph_base}/traversers/customizedpaths",
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

    # ------------------------------------------------------------------
    # Graph Import / Export
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Graph management
    # ------------------------------------------------------------------

    async def ensure_graph(self) -> bool:
        """Create the graph if it doesn't already exist.

        Returns True if graph exists (or was created), False if creation
        failed.
        """
        try:
            graphs = await self.list_graphs()
            if self._config.graph_name in graphs:
                logger.debug("Graph '%s' already exists", self._config.graph_name)
                return True
        except (ConnectionError, httpx.HTTPStatusError, OSError):
            pass

        try:
            body: dict[str, Any] = {
                "gremlin.graph": "org.apache.hugegraph.HugeFactory",
                "backend": "hstore",
                "serializer": "binary",
                "store": self._config.graph_name,
                "task.scheduler_type": "distributed",
            }
            resp = await self._post(
                f"/graphspaces/DEFAULT/graphs/{self._config.graph_name}",
                json_data=body,
            )
            if resp.status_code in (200, 201, 202):
                logger.info("Created graph '%s'", self._config.graph_name)
                return True
            logger.warning(
                "Graph creation returned %d: %s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        except (ConnectionError, httpx.HTTPStatusError, OSError, TimeoutError) as exc:
            logger.warning("Failed to create graph '%s': %s", self._config.graph_name, exc)
            return False

    async def list_graphs(self) -> list[str]:
        """List all graphs in the HugeGraph instance."""
        resp = await self._get("/graphs")
        return resp.json().get("graphs", [])

    async def graph_exists(self) -> bool:
        """Check if the configured graph exists."""
        try:
            graphs = await self.list_graphs()
            return self._config.graph_name in graphs
        except (ConnectionError, httpx.HTTPStatusError, OSError):
            return False

    async def clear(self) -> None:
        """Clear all data from the graph (schema + vertices + edges).

        This is equivalent to dropping and re-creating the graph.
        """
        if not await self.graph_exists():
            return
        try:
            resp = await self._post(
                f"/graphspaces/DEFAULT/graphs/{self._config.graph_name}/clear",
                json_data={"confirm_message": "I'm sure to delete all data"},
            )
            if resp.status_code in (200, 202):
                logger.info("Graph '%s' cleared", self._config.graph_name)
                return
        except httpx.HTTPError:
            pass
        # Fallback: try legacy clear endpoint
        try:
            resp = await self._delete(
                f"/graphspaces/DEFAULT/graphs/{self._config.graph_name}/clear"
                "?confirm_message=I'm+sure+to+delete+all+data"
            )
            if resp.status_code not in (200, 202):
                raise KGError(
                    error_code=ErrorCode.KG_QUERY_FAILED,
                    message=f"Clear graph failed: {resp.text}",
                )
        except httpx.HTTPError as exc:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Failed to clear graph: {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # Schema operations
    # ------------------------------------------------------------------

    async def ensure_schema(self, schema: dict[str, Any]) -> None:
        """Create the graph if needed, then create schema elements in dependency order.

        Schema dict must contain:
        - property_keys: list of {name, data_type, cardinality}
        - vertex_labels: list of {name, id_strategy, primary_keys, properties}
        - edge_labels: list of {name, source_label, target_label}
        - index_labels: list of {name, base_type, base_value, index_type, fields}

        Ignores 400 errors (element already exists) and accepts 202 (async).
        """
        await self.ensure_graph()
        base = self._graph_base + "/schema"

        # PropertyKeys — must be created before vertex/edge labels reference them
        for pk in schema.get("property_keys", []):
            try:
                resp = await self._post(f"{base}/propertykeys", json_data=pk)
                if resp.status_code not in (200, 201, 202, 400):
                    raise KGError(
                        error_code=ErrorCode.KG_SCHEMA_ERROR,
                        message=f"PropertyKey creation failed: {pk['name']}",
                        context={"status_code": resp.status_code, "detail": resp.text[:200]},
                    )
            except KGError:
                raise

        # VertexLabels
        for vl in schema.get("vertex_labels", []):
            try:
                resp = await self._post(f"{base}/vertexlabels", json_data=vl)
                if resp.status_code not in (200, 201, 202, 400):
                    raise KGError(
                        error_code=ErrorCode.KG_SCHEMA_ERROR,
                        message=f"VertexLabel creation failed: {vl['name']}",
                        context={"status_code": resp.status_code, "detail": resp.text[:200]},
                    )
            except KGError:
                raise

        # EdgeLabels
        for el in schema.get("edge_labels", []):
            try:
                resp = await self._post(f"{base}/edgelabels", json_data=el)
                if resp.status_code not in (200, 201, 202, 400):
                    raise KGError(
                        error_code=ErrorCode.KG_SCHEMA_ERROR,
                        message=f"EdgeLabel creation failed: {el['name']}",
                        context={"status_code": resp.status_code, "detail": resp.text[:200]},
                    )
            except KGError:
                raise

        # IndexLabels
        for il in schema.get("index_labels", []):
            try:
                resp = await self._post(f"{base}/indexlabels", json_data=il)
                if resp.status_code not in (200, 201, 202, 400):
                    raise KGError(
                        error_code=ErrorCode.KG_SCHEMA_ERROR,
                        message=f"IndexLabel creation failed: {il['name']}",
                        context={"status_code": resp.status_code, "detail": resp.text[:200]},
                    )
            except KGError:
                raise

    async def get_schema(self) -> dict[str, Any]:
        """Get the current graph schema (vertex labels + edge labels)."""
        try:
            resp = await self._get(f"{self._graph_base}/schema")
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code != 200:
            raise KGError(
                error_code=ErrorCode.KG_SCHEMA_ERROR,
                message=f"Failed to get schema: {resp.text}",
            )
        return resp.json()

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    async def get_stats(self) -> dict[str, Any]:
        """Get vertex and edge counts via REST API.

        HugeGraph REST API does not return a 'total' field in list responses,
        so we fetch with a large limit and count the returned items.
        """
        v_count = 0
        e_count = 0
        try:
            v_resp = await self._get(
                f"{self._graph_base}/graph/vertices?limit=100000"
            )
            v_count = len(v_resp.json().get("vertices", []))
        except (ConnectionError, httpx.HTTPStatusError, KeyError, ValueError):
            v_count = 0
        try:
            e_resp = await self._get(
                f"{self._graph_base}/graph/edges?limit=100000"
            )
            e_count = len(e_resp.json().get("edges", []))
        except (ConnectionError, httpx.HTTPStatusError, KeyError, ValueError):
            e_count = 0

        return {
            "total_vertices": v_count,
            "total_edges": e_count,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
