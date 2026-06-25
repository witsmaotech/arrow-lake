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
import logging
import re
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from arrow_lake.config import HugeGraphConfig
from arrow_lake.exceptions import ErrorCode, KGError
from arrow_lake.knowledge_graph._import_export import _ImportExportMixin
from arrow_lake.knowledge_graph._traversers import _TraverserMixin

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


class HugeGraphClient(_TraverserMixin, _ImportExportMixin):
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
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError, ConnectionResetError)),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        """GET with tenacity retry on transient failures."""
        resp = await self._client.get(path, params=params)
        if resp.status_code >= 500:
            raise httpx.HTTPStatusError(f"Server error {resp.status_code}", request=resp.request, response=resp)
        return resp

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError, ConnectionResetError)),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _post(self, path: str, json_data: Any = None) -> httpx.Response:
        """POST with tenacity retry on transient failures."""
        resp = await self._client.post(path, json=json_data)
        if resp.status_code >= 500:
            raise httpx.HTTPStatusError(f"Server error {resp.status_code}", request=resp.request, response=resp)
        return resp

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError, ConnectionResetError)),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _delete(self, path: str) -> httpx.Response:
        """DELETE with tenacity retry on transient failures."""
        resp = await self._client.delete(path)
        if resp.status_code >= 500:
            raise httpx.HTTPStatusError(f"Server error {resp.status_code}", request=resp.request, response=resp)
        return resp

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
        except (
            ConnectionError,
            httpx.HTTPStatusError,
            OSError,
            TimeoutError,
            httpx.TimeoutException,
        ) as exc:
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

        Equivalent to dropping and re-creating the graph. Tries POST first
        (older HugeGraph); falls back to DELETE (HugeGraph 1.7 PD mode returns
        204 No Content on success).
        """
        if not await self.graph_exists():
            return
        confirm = "I'm sure to delete all data"
        # Primary path (older HugeGraph): POST .../clear
        try:
            resp = await self._post(
                f"/graphspaces/DEFAULT/graphs/{self._config.graph_name}/clear",
                json_data={"confirm_message": confirm},
            )
            if resp.status_code in (200, 202, 204):
                logger.info("Graph '%s' cleared (POST)", self._config.graph_name)
                return
            # Non-success (e.g. 405 in PD mode) — log before falling back so
            # auth/permission errors (401/403) are not silently masked.
            logger.debug(
                "clear POST returned %s; falling back to DELETE", resp.status_code
            )
        except httpx.HTTPError as exc:
            logger.debug("clear POST raised %s; falling back to DELETE", exc)
        # Fallback path (HugeGraph 1.7 PD mode): DELETE .../clear → 204
        try:
            resp = await self._delete(
                f"/graphspaces/DEFAULT/graphs/{self._config.graph_name}/clear"
                "?confirm_message=I'm+sure+to+delete+all+data"
            )
            if resp.status_code in (200, 202, 204):
                logger.info(
                    "Graph '%s' cleared (DELETE %s)",
                    self._config.graph_name,
                    resp.status_code,
                )
                return
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

        # PropertyKeys -- must be created before vertex/edge labels reference them
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
