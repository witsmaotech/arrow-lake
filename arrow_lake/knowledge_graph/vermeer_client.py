"""HugeGraph-Vermeer OLAP algorithm engine client.

REST API client for HugeGraph-Vermeer, providing graph algorithm jobs
(PageRank, Louvain, WCC, etc.) via async HTTP calls.

Reference: dev_notes/hugegraph_build_skills/batch01/hugegraph-vermeer.md
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from arrow_lake.config import HugeGraphConfig
from arrow_lake.exceptions import ErrorCode, KGError

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RETRIES = 3
_POLL_INTERVAL = 1.0
_MAX_POLL_ATTEMPTS = 600


class VermeerClient:
    """Client for HugeGraph-Vermeer OLAP algorithm engine."""

    def __init__(self, config: HugeGraphConfig) -> None:
        self._base_url = f"http://{config.vermeer_host}:{config.vermeer_port}"
        self._graph_name = config.graph_name
        from arrow_lake.core.http import create_async_http_client

        self._client = create_async_http_client(
            base_url=self._base_url,
            timeout=httpx.Timeout(config.timeout_seconds),
            http2=False,
        )

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _post(self, path: str, json_data: Any = None) -> httpx.Response:
        return await self._client.post(path, json=json_data)

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _get(self, path: str) -> httpx.Response:
        return await self._client.get(path)

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        stop=stop_after_attempt(_DEFAULT_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _delete(self, path: str) -> httpx.Response:
        return await self._client.delete(path)

    def _handle_error(self, exc: httpx.HTTPError) -> None:
        raise KGError(
            error_code=ErrorCode.KG_CONNECTION_FAILED,
            message=f"HTTP error calling Vermeer at {self._base_url}: {exc}",
            context={"host": self._base_url},
        ) from exc

    # ------------------------------------------------------------------
    # Job Management
    # ------------------------------------------------------------------

    async def submit_job(
        self, algorithm: str, graph_name: str, **params: Any,
    ) -> str:
        """Submit an algorithm job. Returns job_id."""
        body: dict[str, Any] = {
            "algorithm": algorithm,
            "graph_name": graph_name,
        }
        if params:
            body["parameters"] = params

        try:
            resp = await self._post("/api/v1/jobs", json_data=body)
        except httpx.HTTPError as exc:
            self._handle_error(exc)

        if resp.status_code not in (200, 201, 202):
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Vermeer job submission failed: {resp.text}",
                context={"algorithm": algorithm, "status_code": resp.status_code},
            )

        data = resp.json()
        return str(data.get("job_id", data.get("id", "")))

    async def job_status(self, job_id: str) -> dict[str, Any]:
        """Get job status. Returns {task_status, progress, ...}."""
        try:
            resp = await self._get(f"/api/v1/jobs/{job_id}")
        except httpx.HTTPError as exc:
            self._handle_error(exc)

        if resp.status_code != 200:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Vermeer job status failed: {resp.text}",
                context={"job_id": job_id},
            )

        return resp.json()

    async def job_results(self, job_id: str) -> dict[str, Any]:
        """Get completed job results."""
        try:
            resp = await self._get(f"/api/v1/jobs/{job_id}/results")
        except httpx.HTTPError as exc:
            self._handle_error(exc)

        if resp.status_code != 200:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Vermeer job results failed: {resp.text}",
                context={"job_id": job_id},
            )

        return resp.json()

    async def cancel_job(self, job_id: str) -> None:
        """Cancel a running job."""
        try:
            resp = await self._delete(f"/api/v1/jobs/{job_id}")
        except httpx.HTTPError as exc:
            self._handle_error(exc)

        if resp.status_code not in (200, 202, 204):
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message=f"Vermeer job cancel failed: {resp.text}",
                context={"job_id": job_id},
            )

    async def _wait_for_job(self, job_id: str) -> dict[str, Any]:
        """Poll job status until completion. Returns final results."""
        for _ in range(_MAX_POLL_ATTEMPTS):
            status = await self.job_status(job_id)
            task_status = status.get("task_status", "").lower()

            if task_status in ("completed", "success", "done"):
                return await self.job_results(job_id)
            if task_status in ("failed", "cancelled", "canceled"):
                raise KGError(
                    error_code=ErrorCode.KG_QUERY_FAILED,
                    message=f"Vermeer job {job_id} failed: {status}",
                    context={"job_id": job_id, "status": status},
                )

            await asyncio.sleep(_POLL_INTERVAL)

        raise KGError(
            error_code=ErrorCode.KG_QUERY_FAILED,
            message=f"Vermeer job {job_id} timed out after {_MAX_POLL_ATTEMPTS}s",
            context={"job_id": job_id},
        )

    # ------------------------------------------------------------------
    # High-level Algorithm Methods
    # ------------------------------------------------------------------

    async def pagerank(
        self,
        graph_name: str | None = None,
        *,
        iterations: int = 20,
        damping_factor: float = 0.85,
    ) -> dict[str, Any]:
        """PageRank — identify important vertices."""
        gn = graph_name or self._graph_name
        job_id = await self.submit_job(
            "pagerank", gn,
            iterations=iterations, damping_factor=damping_factor,
        )
        return await self._wait_for_job(job_id)

    async def louvain(
        self,
        graph_name: str | None = None,
        *,
        resolution: float = 1.0,
    ) -> dict[str, Any]:
        """Louvain community detection."""
        gn = graph_name or self._graph_name
        job_id = await self.submit_job("louvain", gn, resolution=resolution)
        return await self._wait_for_job(job_id)

    async def label_propagation(
        self,
        graph_name: str | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        """Label Propagation community detection."""
        gn = graph_name or self._graph_name
        job_id = await self.submit_job("label_propagation", gn, **params)
        return await self._wait_for_job(job_id)

    async def wcc(self, graph_name: str | None = None) -> dict[str, Any]:
        """Weakly Connected Components."""
        gn = graph_name or self._graph_name
        job_id = await self.submit_job("wcc", gn)
        return await self._wait_for_job(job_id)

    async def triangle_count(self, graph_name: str | None = None) -> dict[str, Any]:
        """Triangle Counting for clustering coefficient analysis."""
        gn = graph_name or self._graph_name
        job_id = await self.submit_job("triangle_count", gn)
        return await self._wait_for_job(job_id)

    async def degree_centrality(
        self, graph_name: str | None = None,
    ) -> dict[str, Any]:
        """Degree Centrality for connectivity analysis."""
        gn = graph_name or self._graph_name
        job_id = await self.submit_job("degree_centrality", gn)
        return await self._wait_for_job(job_id)

    async def closeness_centrality(
        self, graph_name: str | None = None,
    ) -> dict[str, Any]:
        """Closeness Centrality for influence analysis."""
        gn = graph_name or self._graph_name
        job_id = await self.submit_job("closeness_centrality", gn)
        return await self._wait_for_job(job_id)

    async def k_core(
        self,
        graph_name: str | None = None,
        *,
        k: int = 3,
    ) -> dict[str, Any]:
        """K-Core decomposition."""
        gn = graph_name or self._graph_name
        job_id = await self.submit_job("k_core", gn, k=k)
        return await self._wait_for_job(job_id)

    async def betweenness_centrality(
        self, graph_name: str | None = None,
    ) -> dict[str, Any]:
        """Betweenness Centrality for bridge node analysis."""
        gn = graph_name or self._graph_name
        job_id = await self.submit_job("betweenness_centrality", gn)
        return await self._wait_for_job(job_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
