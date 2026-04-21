"""HugeGraph REST API client.

Async HTTP client for HugeGraph 1.7.0, using httpx + tenacity retry.
Does NOT depend on the hugegraph-client SDK.

Reference: memory/project_hugegraph_api_findings.md
Key API differences from local docs:
- Gremlin endpoint: POST /gremlin (NOT /graphs/{name}/gremlin)
- Traversal source: {graph_name}.traversal() (NOT g.V())
- Edge fields: outV/outVLabel/inV/inVLabel (NOT source/target)
- Schema creation returns 202 (async accepted)
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from arrow_lake.config import HugeGraphConfig
from arrow_lake.exceptions import ErrorCode, KGError

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RETRIES = 3


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

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(config.timeout_seconds),
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
    async def _get(self, path: str) -> httpx.Response:
        """GET with tenacity retry on transient failures."""
        return await self._client.get(path)

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

    async def add_vertices(self, vertices: list[dict[str, Any]]) -> int:
        """Batch insert vertices. Returns count of inserted vertices."""
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
        return len(ids) if isinstance(ids, list) else 0

    async def get_vertex(self, vertex_id: str) -> dict[str, Any] | None:
        """Get a vertex by ID. Returns None if not found."""
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

    # ------------------------------------------------------------------
    # Schema operations
    # ------------------------------------------------------------------

    async def ensure_schema(self, schema: dict[str, Any]) -> None:
        """Create schema elements in dependency order.

        Schema dict must contain:
        - property_keys: list of {name, data_type, cardinality}
        - vertex_labels: list of {name, id_strategy, primary_keys, properties}
        - edge_labels: list of {name, source_label, target_label}
        - index_labels: list of {name, base_type, base_value, index_type, fields}

        Ignores 400 errors (element already exists) and accepts 202 (async).
        """
        base = self._graph_base + "/schema"

        # PropertyKeys
        for pk in schema.get("property_keys", []):
            try:
                await self._post(f"{base}/propertykeys", json_data=pk)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (400,):
                    raise KGError(
                        error_code=ErrorCode.KG_SCHEMA_ERROR,
                        message=f"PropertyKey creation failed: {pk['name']}",
                        context={"detail": exc.response.text},
                    ) from exc

        # VertexLabels
        for vl in schema.get("vertex_labels", []):
            try:
                await self._post(f"{base}/vertexlabels", json_data=vl)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (400,):
                    raise KGError(
                        error_code=ErrorCode.KG_SCHEMA_ERROR,
                        message=f"VertexLabel creation failed: {vl['name']}",
                        context={"detail": exc.response.text},
                    ) from exc

        # EdgeLabels
        for el in schema.get("edge_labels", []):
            try:
                await self._post(f"{base}/edgelabels", json_data=el)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (400,):
                    raise KGError(
                        error_code=ErrorCode.KG_SCHEMA_ERROR,
                        message=f"EdgeLabel creation failed: {el['name']}",
                        context={"detail": exc.response.text},
                    ) from exc

        # IndexLabels
        for il in schema.get("index_labels", []):
            try:
                await self._post(f"{base}/indexlabels", json_data=il)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (400,):
                    raise KGError(
                        error_code=ErrorCode.KG_SCHEMA_ERROR,
                        message=f"IndexLabel creation failed: {il['name']}",
                        context={"detail": exc.response.text},
                    ) from exc

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
        """Get vertex and edge counts via Gremlin."""
        g = self._config.graph_name
        v_data = await self.gremlin(f"{g}.traversal().V().count()")
        e_data = await self.gremlin(f"{g}.traversal().E().count()")

        v_count = v_data[0] if v_data else 0
        e_count = e_data[0] if e_data else 0

        return {
            "total_vertices": v_count,
            "total_edges": e_count,
        }

    # ------------------------------------------------------------------
    # Graph management
    # ------------------------------------------------------------------

    async def clear(self) -> None:
        """Clear all data from the graph. Use with caution."""
        try:
            resp = await self._delete(
                f"/graphspaces/DEFAULT/graphs/{self._config.graph_name}/clear"
                "?confirm_message=I'm+sure+to+delete+all+data"
            )
        except httpx.HTTPError as exc:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Failed to clear graph: {exc}",
            ) from exc

        if resp.status_code not in (200, 202):
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Clear graph failed: {resp.text}",
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
