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

import asyncio
import base64
import json
import logging
import re
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from arrow_lake.config import HugeGraphConfig
from arrow_lake.exceptions import ErrorCode, KGError
from arrow_lake.knowledge_graph._import_export import _ImportExportMixin
from arrow_lake.knowledge_graph._traversers import _TraverserMixin

logger = logging.getLogger(__name__)

# Safe vertex ID pattern: alphanumeric, CJK, underscore, hyphen, dot, space.
_SAFE_VERTEX_ID_RE = re.compile(r"^[a-zA-Z0-9_\-一-鿿　-〿＀-￯:.\s]+$")

_DEFAULT_MAX_RETRIES = 3

# Exceptions eligible for tenacity retry in _get/_post/_delete. Network blips
# (timeout / connect / reset) always retry. 5xx is retried ONLY when transient —
# deterministic HugeGraph failures (OutOfMemoryError, IllegalArgument, ...) are
# not, because re-issuing the same query repeats the failure and, for OOM,
# hammers the HugeGraph heap once per retry (3× per request under the old policy).
# 4xx is returned to the caller unchecked and never reaches this predicate.
def _is_retryable_http_error(exc: BaseException) -> bool:
    """Tenacity retry predicate for _get/_post/_delete."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, ConnectionResetError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        body = (getattr(exc.response, "text", "") or "")[:2048].lower()
        non_transient = (
            "outofmemory", "heap space", "stackoverflowerror",
            "illegalargumentexception", "illegalstateexception",
            "nosuchelementexception", "unmodifiable", "unsupportedoperation",
        )
        return not any(k in body for k in non_transient)
    return False

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

    def _graph_base_for(self, graph_name: str | None) -> str:
        """Return ``/graphs/{name}`` for the given graph.

        Falls back to the configured default ``graph_name`` when None, so
        existing callers (single-graph mode) keep their behavior. Per-dataset
        isolation passes the derived ``kg_{dataset}`` name here.
        """
        name = graph_name or self._config.graph_name
        return f"/graphs/{name}"

    async def _wait_graph_ready(
        self, name: str, *, attempts: int = 30, delay: float = 0.5
    ) -> None:
        """Poll a newly created graph's schema endpoint until it responds.

        HugeGraph 1.7 PD creates the graph asynchronously; the hstore backend
        needs a brief moment before it accepts schema writes. Without this,
        ``ensure_schema`` immediately after ``ensure_graph`` can 500 on a
        brand-new per-dataset graph.
        """
        for _ in range(attempts):
            try:
                resp = await self._client.get(f"/graphs/{name}/schema")
                if resp.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(delay)
        logger.warning(
            "Graph '%s' schema endpoint not ready after %.1fs — proceeding",
            name,
            attempts * delay,
        )

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        """GET with tenacity retry on transient failures."""
        resp = await self._client.get(path, params=params)
        if resp.status_code >= 500:
            raise httpx.HTTPStatusError(
                f"Server error {resp.status_code}: {resp.text[:500]}",
                request=resp.request, response=resp,
            )
        return resp

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _post(self, path: str, json_data: Any = None) -> httpx.Response:
        """POST with tenacity retry on transient failures."""
        resp = await self._client.post(path, json=json_data)
        if resp.status_code >= 500:
            raise httpx.HTTPStatusError(
                f"Server error {resp.status_code}: {resp.text[:500]}",
                request=resp.request, response=resp,
            )
        return resp

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _delete(self, path: str) -> httpx.Response:
        """DELETE with tenacity retry on transient failures."""
        resp = await self._client.delete(path)
        if resp.status_code >= 500:
            raise httpx.HTTPStatusError(
                f"Server error {resp.status_code}: {resp.text[:500]}",
                request=resp.request, response=resp,
            )
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

    async def add_vertices(
        self, vertices: list[dict[str, Any]], *, graph_name: str | None = None
    ) -> list[str]:
        """Batch insert vertices. Returns list of created vertex IDs."""
        try:
            resp = await self._post(
                f"{self._graph_base_for(graph_name)}/graph/vertices/batch",
                json_data=vertices,
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

    async def get_vertex(
        self, vertex_id: str, *, graph_name: str | None = None
    ) -> dict[str, Any] | None:
        """Get a vertex by ID. Returns None if not found."""
        if not _SAFE_VERTEX_ID_RE.match(vertex_id):
            logger.warning("Rejected unsafe vertex_id: %r", vertex_id)
            return None
        try:
            resp = await self._get(
                f'{self._graph_base_for(graph_name)}/graph/vertices/"{vertex_id}"'
            )
        except httpx.HTTPError:
            return None

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            return None
        return resp.json()

    async def find_vertices_by_property(
        self,
        label: str | None,
        properties: dict[str, Any],
        *,
        graph_name: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Find vertices by property values via REST (label optional).

        ``GET /graphs/{name}/graph/vertices?properties={..}`` — ``label`` is
        added only when provided, so a label-less query matches across all
        vertex labels (used for GraphRAG anchor resolution on large graphs
        where the vertex label scheme is unknown). Replaces the gremlin
        ``find_entity`` query so it works on dynamically created per-dataset
        graphs (which are not gremlin-bound). Returns the matching ``vertices``
        list (empty if none / graph missing).
        """
        # Hardening: label-agnostic queries (used by GraphRAG anchor resolution)
        # are restricted to a safe property-key allowlist + bounded value length,
        # preventing overly broad / enumeration-style queries.
        if label is None:
            bad = [k for k in properties if k not in ("name", "label")]
            if bad:
                raise KGError(
                    error_code=ErrorCode.KG_QUERY_FAILED,
                    message=(
                        "label-agnostic vertex query allows only 'name'/'label' "
                        f"property keys; rejected: {bad}"
                    ),
                    context={"rejected_keys": bad},
                )
        bounded = {str(k): str(v)[: 256] for k, v in properties.items()}
        params = {
            "properties": json.dumps(bounded),
            "limit": str(limit),
        }
        if label:
            params["label"] = label
        try:
            resp = await self._get(
                f"{self._graph_base_for(graph_name)}/graph/vertices",
                params=params,
            )
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code == 404:
            return []  # graph or vertex not found → treat as empty
        if resp.status_code != 200:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"find_vertices_by_property failed: {resp.text}",
                context={"label": label, "status_code": resp.status_code},
            )
        return resp.json().get("vertices", [])

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    async def add_edges(
        self, edges: list[dict[str, Any]], *, graph_name: str | None = None
    ) -> int:
        """Batch insert edges. Returns count of inserted edges.

        Each edge must include: label, outV, outVLabel, inV, inVLabel, properties.
        """
        try:
            resp = await self._post(
                f"{self._graph_base_for(graph_name)}/graph/edges/batch",
                json_data=edges,
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

    async def ensure_graph(self, *, graph_name: str | None = None) -> bool:
        """Create the graph if it doesn't already exist.

        Returns True if graph exists (or was created), False if creation
        failed.
        """
        name = graph_name or self._config.graph_name
        try:
            graphs = await self.list_graphs()
            if name in graphs:
                logger.debug("Graph '%s' already exists", name)
                return True
        except (ConnectionError, httpx.HTTPStatusError, OSError):
            pass

        try:
            # Backend + scheduler + factory must match the deployment.
            #  • gremlin.graph: HugeFactoryAuthProxy because auth is enabled
            #    (matches the working default graph; the bare HugeFactory fails
            #    to instantiate under auth).
            #  • scheduler: hstore/PD uses `distributed`; rocksdb single-node
            #    uses `local`.
            #  • rocksdb: each graph MUST get its own data_path — otherwise every
            #    graph collides on the default `rocksdb-data/data/` directory and
            #    the 2nd+ graph fails with a RocksDB lock conflict
            #    ("lock hold by current process ... No locks available").
            backend = getattr(self._config, "backend", "rocksdb") or "rocksdb"
            body: dict[str, Any] = {
                "gremlin.graph": "org.apache.hugegraph.auth.HugeFactoryAuthProxy",
                "backend": backend,
                "serializer": "binary",
                "store": name,
                "task.scheduler_type": "distributed" if backend == "hstore" else "local",
            }
            if backend == "rocksdb":
                root = (
                    getattr(self._config, "rocksdb_data_path", "/var/lib/hugegraph")
                    or "/var/lib/hugegraph"
                )
                body["rocksdb.data_path"] = f"{root}/graphs/{name}"
                body["rocksdb.wal_path"] = f"{root}/graphs/{name}"
            resp = await self._post(
                f"/graphspaces/DEFAULT/graphs/{name}",
                json_data=body,
            )
            if resp.status_code in (200, 201, 202):
                logger.info("Created graph '%s'", name)
                await self._wait_graph_ready(name)
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
            # Graph creation is slow (rocksdb data_path init + schema). The
            # POST often times out client-side even though the server created
            # the graph — re-verify before declaring failure to avoid a
            # false-negative WARNING (seen recurring: build proceeds fine on
            # the next schema POST, but the log scares). Mirrors the top-of-
            # method existence check.
            try:
                graphs = await self.list_graphs()
                if name in graphs:
                    logger.info(
                        "Graph '%s' created (verified after slow/failed POST: %s)",
                        name,
                        exc,
                    )
                    return True
            except (ConnectionError, httpx.HTTPStatusError, OSError):
                pass
            logger.warning("Failed to create graph '%s': %s", name, exc)
            return False

    async def list_graphs(self) -> list[str]:
        """List all graphs in the HugeGraph instance.

        In PD mode (``usePD=true``), the flat ``GET /graphs`` endpoint returns
        empty because graphs are managed per-graphspace; fall back to the
        DEFAULT graphspace listing so existence checks (``ensure_graph`` /
        ``graph_exists``) keep working.
        """
        resp = await self._get("/graphs")
        graphs = resp.json().get("graphs", [])
        if graphs:
            return graphs
        try:
            gs_resp = await self._get("/graphspaces/DEFAULT/graphs")
            if gs_resp.status_code == 200:
                return gs_resp.json().get("graphs", [])
        except (httpx.HTTPError, ValueError):
            pass
        return []

    async def graph_exists(self, *, graph_name: str | None = None) -> bool:
        """Check if the configured graph exists."""
        name = graph_name or self._config.graph_name
        try:
            graphs = await self.list_graphs()
            return name in graphs
        except (ConnectionError, httpx.HTTPStatusError, OSError):
            return False

    async def clear(self, *, graph_name: str | None = None) -> None:
        """Clear all data from the graph (schema + vertices + edges).

        Equivalent to dropping and re-creating the graph. Tries POST first
        (older HugeGraph); falls back to DELETE (HugeGraph 1.7 PD mode returns
        204 No Content on success).
        """
        name = graph_name or self._config.graph_name
        if not await self.graph_exists(graph_name=name):
            return
        confirm = "I'm sure to delete all data"
        # Primary path (older HugeGraph): POST .../clear
        try:
            resp = await self._post(
                f"/graphspaces/DEFAULT/graphs/{name}/clear",
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
                f"/graphspaces/DEFAULT/graphs/{name}/clear"
                "?confirm_message=I'm+sure+to+delete+all+data"
            )
            if resp.status_code in (200, 202, 204):
                logger.info(
                    "Graph '%s' cleared (DELETE %s)",
                    name,
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

    async def drop_graph(self, graph_name: str) -> bool:
        """Drop a graph entirely (data + schema + graph shell).

        ``DELETE /graphspaces/DEFAULT/graphs/{name}?confirm_message=..``.
        Distinct from ``clear()`` which only empties data but leaves the graph
        shell. Used for drop-on-dataset-delete. Returns True on success;
        raises ``KGError`` on unexpected failure (caller wraps for idempotency).
        """
        try:
            resp = await self._delete(
                f"/graphspaces/DEFAULT/graphs/{graph_name}"
                "?confirm_message=I'm+sure+to+drop+the+graph"
            )
        except httpx.HTTPError as exc:
            self._handle_http_error(exc)

        if resp.status_code in (200, 202, 204):
            return True
        raise KGError(
            error_code=ErrorCode.KG_QUERY_FAILED,
            message=f"Drop graph '{graph_name}' failed: {resp.text}",
            context={"graph_name": graph_name, "status_code": resp.status_code},
        )

    # ------------------------------------------------------------------
    # Schema operations
    # ------------------------------------------------------------------

    async def _post_schema_element(
        self, base: str, kind: str, payload: dict[str, Any]
    ) -> None:
        """POST one schema element. 400 = already exists (ignored, idempotent).

        5xx → KG_SCHEMA_ERROR with restart hint: HugeGraph GraphManager 内存 schema 缓存
        在运行期 clear/drop 后不刷新(只改持久层),propertykeys POST 返回 500(非 400)。
        此时 KG build 必 FAILED,需 restart hg-server 让 GraphManager 从 rocksdb 重载。
        """
        try:
            resp = await self._post(f"{base}/{kind}", json_data=payload)
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise KGError(
                    error_code=ErrorCode.KG_SCHEMA_ERROR,
                    message=(
                        f"HugeGraph schema {kind} POST {e.response.status_code}: GraphManager dirty state "
                        f"(clear/drop 后内存 schema 缓存未刷新)。restart hg-server 后重试: "
                        f"`make kg-clear-graph DS=<ds>` 或 `docker restart <hg-server>`"
                    ),
                    context={
                        "kind": kind, "name": payload.get("name"),
                        "status": e.response.status_code, "detail": e.response.text[:200],
                    },
                ) from e
            raise
        if resp.status_code not in (200, 201, 202, 400):
            raise KGError(
                error_code=ErrorCode.KG_SCHEMA_ERROR,
                message=f"{kind} creation failed: {payload.get('name')}",
                context={"status_code": resp.status_code, "detail": resp.text[:200]},
            )

    async def ensure_schema(
        self, schema: dict[str, Any], *, graph_name: str | None = None
    ) -> None:
        """Create the graph if needed, then create schema elements in dependency order.

        Schema dict must contain:
        - property_keys: list of {name, data_type, cardinality}
        - vertex_labels: list of {name, id_strategy, primary_keys, properties}
        - edge_labels: list of {name, source_label, target_label}
        - index_labels: list of {name, base_type, base_value, index_type, fields}

        Ignores 400 errors (element already exists) and accepts 202 (async).
        5xx (GraphManager dirty after clear/drop) → KG_SCHEMA_ERROR with restart hint.
        """
        await self.ensure_graph(graph_name=graph_name)
        base = self._graph_base_for(graph_name) + "/schema"

        # 依赖序: PropertyKeys → VertexLabels → EdgeLabels → IndexLabels
        for pk in schema.get("property_keys", []):
            await self._post_schema_element(base, "propertykeys", pk)
        for vl in schema.get("vertex_labels", []):
            await self._post_schema_element(base, "vertexlabels", vl)
        for el in schema.get("edge_labels", []):
            await self._post_schema_element(base, "edgelabels", el)
        for il in schema.get("index_labels", []):
            await self._post_schema_element(base, "indexlabels", il)

    async def get_schema(self, *, graph_name: str | None = None) -> dict[str, Any]:
        """Get the current graph schema (vertex labels + edge labels)."""
        try:
            resp = await self._get(f"{self._graph_base_for(graph_name)}/schema")
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

    async def get_stats(self, *, graph_name: str | None = None) -> dict[str, Any]:
        """Get vertex and edge counts via REST API.

        HugeGraph REST API does not return a 'total' field in list responses,
        so we fetch with a large limit and count the returned items.
        """
        base = self._graph_base_for(graph_name)
        v_count = 0
        e_count = 0
        try:
            v_resp = await self._get(
                f"{base}/graph/vertices?limit=100000"
            )
            v_count = len(v_resp.json().get("vertices", []))
        except (ConnectionError, httpx.HTTPStatusError, KeyError, ValueError):
            v_count = 0
        try:
            e_resp = await self._get(
                f"{base}/graph/edges?limit=100000"
            )
            e_count = len(e_resp.json().get("edges", []))
        except (ConnectionError, httpx.HTTPStatusError, KeyError, ValueError):
            e_count = 0

        return {
            "total_vertices": v_count,
            "total_edges": e_count,
        }

    async def get_graph_snapshot(
        self, *, graph_name: str | None = None, limit: int = 300,
        label: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return raw vertices + edges (capped) for graph visualization.

        Fetches ``limit + 1`` vertices so the caller can detect truncation;
        edges fetch uses a larger cap (``limit * 5``) since edges are filtered
        client-side to only those whose endpoints are both in the vertex set.
        ``label`` (optional) restricts vertices to one vertex label — used by
        RAG to fetch only ``entity`` vertices (chunk vertices ``2:*`` sort
        before entity vertices ``3:*`` by id and carry heavy ``content``, so an
        unfiltered snapshot either misses entities or is slow/bloated). Edges
        are then naturally filtered to those between same-label vertices.
        Best-effort: transient REST errors return an empty list for that side.
        """
        base = self._graph_base_for(graph_name)
        v_q = f"limit={limit + 1}" + (f"&label={label}" if label else "")
        vertices: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        try:
            v_resp = await self._get(f"{base}/graph/vertices?{v_q}")
            vertices = v_resp.json().get("vertices", [])
        except (ConnectionError, httpx.HTTPStatusError, KeyError, ValueError):
            vertices = []
        try:
            e_resp = await self._get(f"{base}/graph/edges?limit={int(limit) * 5 + 1}")
            edges = e_resp.json().get("edges", [])
        except (ConnectionError, httpx.HTTPStatusError, KeyError, ValueError):
            edges = []
        return vertices, edges

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
