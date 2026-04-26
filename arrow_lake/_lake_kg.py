"""Knowledge graph operations mixin for the Lake facade."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from arrow_lake.exceptions import ErrorCode, KGError

if TYPE_CHECKING:
    from arrow_lake.knowledge_graph.builder import KGBuilder
    from arrow_lake.knowledge_graph.client import HugeGraphClient
    from arrow_lake.knowledge_graph.extractor import EntityExtractor
    from arrow_lake.knowledge_graph.retriever import KGRetriever
    from arrow_lake.knowledge_graph.vermeer_client import VermeerClient

logger = logging.getLogger(__name__)


class _LakeKGMixin:
    """Knowledge graph operations mixin for Lake class.

    Provides methods for building, querying, and managing knowledge graphs
    backed by HugeGraph. All methods require ``hugegraph.enabled=True``
    in the Lake configuration; otherwise they raise ``KGError``.
    """

    # ------------------------------------------------------------------
    # Lazy component accessors
    # ------------------------------------------------------------------

    def _get_kg_client(self) -> HugeGraphClient | None:
        """Lazily create and cache a HugeGraphClient.

        Returns None if KG is not enabled.
        """
        if not self._config.hugegraph.enabled:
            return None

        return self._get_component("kg_client", self._create_kg_client)

    def _get_kg_extractor(self) -> EntityExtractor | None:
        """Lazily create and cache an EntityExtractor.

        Returns None if KG is not enabled.
        """
        if not self._config.hugegraph.enabled:
            return None

        return self._get_component("kg_extractor", self._create_kg_extractor)

    def _get_kg_builder(self) -> KGBuilder | None:
        """Lazily create and cache a KGBuilder.

        Returns None if KG is not enabled.
        """
        if not self._config.hugegraph.enabled:
            return None

        return self._get_component("kg_builder", self._create_kg_builder)

    def _get_kg_retriever(self) -> KGRetriever | None:
        """Lazily create and cache a KGRetriever.

        Returns None if KG is not enabled.
        """
        if not self._config.hugegraph.enabled:
            return None

        return self._get_component("kg_retriever", self._create_kg_retriever)

    def _get_vermeer_client(self) -> VermeerClient | None:
        """Lazily create and cache a VermeerClient.

        Returns None if KG is not enabled.
        """
        if not self._config.hugegraph.enabled:
            return None

        return self._get_component("vermeer_client", self._create_vermeer_client)

    # ------------------------------------------------------------------
    # Component factories
    # ------------------------------------------------------------------

    def _create_kg_client(self) -> HugeGraphClient:
        from arrow_lake.knowledge_graph.client import HugeGraphClient

        return HugeGraphClient(self._config.hugegraph)

    def _create_kg_extractor(self) -> EntityExtractor:
        from arrow_lake.knowledge_graph.extractor import EntityExtractor
        from arrow_lake.rag.provider import create_llm_provider

        llm_provider = create_llm_provider(self._config.llm)
        return EntityExtractor(llm_provider)

    def _create_kg_builder(self) -> KGBuilder:
        from arrow_lake.knowledge_graph.builder import KGBuilder

        client = self._get_kg_client()
        extractor = self._get_kg_extractor()
        if client is None or extractor is None:
            raise KGError(
                error_code=ErrorCode.KG_GRAPH_NOT_FOUND,
                message="Cannot create KGBuilder: KG is not enabled",
            )
        return KGBuilder(client, extractor, self._config.hugegraph)

    def _create_kg_retriever(self) -> KGRetriever:
        from arrow_lake.knowledge_graph.retriever import KGRetriever

        client = self._get_kg_client()
        if client is None:
            raise KGError(
                error_code=ErrorCode.KG_GRAPH_NOT_FOUND,
                message="Cannot create KGRetriever: KG is not enabled",
            )
        return KGRetriever(client, self._config.hugegraph)

    def _create_vermeer_client(self) -> VermeerClient:
        from arrow_lake.knowledge_graph.vermeer_client import VermeerClient

        return VermeerClient(self._config.hugegraph)

    # ------------------------------------------------------------------
    # Guard
    # ------------------------------------------------------------------

    def _ensure_kg_enabled(self) -> None:
        """Raise KGError if KG is not enabled in configuration."""
        if not self._config.hugegraph.enabled:
            raise KGError(
                error_code=ErrorCode.KG_GRAPH_NOT_FOUND,
                message="Knowledge graph is not enabled. Set hugegraph.enabled=true in config.",
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def kg_build(self, dataset_name: str) -> str:
        """Build a knowledge graph from a dataset.

        Reads text chunks from the specified dataset, extracts entities
        and relations via LLM, and inserts them into HugeGraph.

        Args:
            dataset_name: Name of the Lance dataset to build KG from.

        Returns:
            Task ID string for tracking build progress.

        Raises:
            KGError: If KG is not enabled or build fails.
        """
        self._ensure_kg_enabled()

        import pyarrow as pa

        builder = self._get_kg_builder()
        if builder is None:
            raise KGError(
                error_code=ErrorCode.KG_BUILD_FAILED,
                message="KGBuilder is not available",
            )

        storage = self._get_storage()
        dataset = storage.open_dataset(dataset_name)
        table = dataset.search().to_arrow()

        if "id" not in table.column_names:
            table = table.add_column(0, "id", pa.array([str(i) for i in range(table.num_rows)]))
        if "content" not in table.column_names:
            text_col = "text" if "text" in table.column_names else table.column_names[0]
            new_names = ["content" if c == text_col else c for c in table.column_names]
            table = table.rename_columns(new_names)
        if "document_name" not in table.column_names:
            table = table.append_column("document_name", pa.array([dataset_name] * table.num_rows))
        if "chunk_index" not in table.column_names:
            table = table.append_column("chunk_index", pa.array(list(range(table.num_rows))))

        return await builder.build(dataset_name, table)

    async def kg_build_status(self, task_id: str) -> dict[str, Any] | None:
        """Get the status of a KG build task.

        Args:
            task_id: Build task ID returned by kg_build().

        Returns:
            Status dict with task details, or None if task not found.

        Raises:
            KGError: If KG is not enabled.
        """
        self._ensure_kg_enabled()

        builder = self._get_kg_builder()
        if builder is None:
            return None

        task = builder.get_task_status(task_id)
        if task is None:
            return None

        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "dataset_name": task.dataset_name,
            "total_chunks": task.total_chunks,
            "processed_chunks": task.processed_chunks,
            "entity_count": task.entity_count,
            "relation_count": task.relation_count,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "error": task.error,
        }

    async def kg_query(
        self,
        query: str,
        *,
        traversal_depth: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a Gremlin query against the knowledge graph.

        Args:
            query: Gremlin query string.
            traversal_depth: Optional traversal depth limit.

        Returns:
            List of query result dicts.

        Raises:
            KGError: If KG is not enabled or query fails.
        """
        self._ensure_kg_enabled()

        client = self._get_kg_client()
        if client is None:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message="KGClient is not available",
            )

        return await client.gremlin(query)

    async def kg_get_neighbors(
        self,
        entity_id: str,
        depth: int = 1,
    ) -> list[dict[str, Any]]:
        """Get neighbor vertices of a given entity.

        Args:
            entity_id: Vertex ID to start traversal from.
            depth: Traversal depth (number of hops).

        Returns:
            List of neighbor vertex dicts.

        Raises:
            KGError: If KG is not enabled or traversal fails.
        """
        self._ensure_kg_enabled()

        client = self._get_kg_client()
        if client is None:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message="KGClient is not available",
            )

        depth = min(depth, self._config.hugegraph.max_traversal_depth)
        return await client.traverser_kneighbor(source=entity_id, depth=depth)

    async def kg_stats(self) -> dict[str, Any]:
        """Get knowledge graph statistics.

        Returns:
            Dict with vertex and edge counts.

        Raises:
            KGError: If KG is not enabled.
        """
        self._ensure_kg_enabled()

        client = self._get_kg_client()
        if client is None:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message="KGClient is not available",
            )

        return await client.get_stats()

    async def kg_delete_graph(self) -> None:
        """Delete all data from the knowledge graph.

        Use with caution -- this operation is irreversible.

        Raises:
            KGError: If KG is not enabled or deletion fails.
        """
        self._ensure_kg_enabled()

        client = self._get_kg_client()
        if client is None:
            raise KGError(
                error_code=ErrorCode.KG_QUERY_FAILED,
                message="KGClient is not available",
            )

        await client.clear()

    # ------------------------------------------------------------------
    # Traverser API (8 methods)
    # ------------------------------------------------------------------

    async def kg_all_shortest_paths(
        self,
        source: str,
        target: str,
        *,
        direction: str = "OUT",
        max_depth: int = 10,
    ) -> list[dict[str, Any]]:
        """All shortest paths between source and target vertices."""
        self._ensure_kg_enabled()
        client = self._get_kg_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="KGClient is not available")
        return await client.traverser_all_shortest_paths(
            source, target, direction=direction, max_depth=max_depth,
        )

    async def kg_weighted_shortest_path(
        self,
        source: str,
        target: str,
        *,
        direction: str = "OUT",
        weight_prop: str = "weight",
        max_degree: int = 10000,
    ) -> dict[str, Any]:
        """Weighted shortest path between source and target."""
        self._ensure_kg_enabled()
        client = self._get_kg_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="KGClient is not available")
        return await client.traverser_weighted_shortest_path(
            source, target, direction=direction,
            weight_prop=weight_prop, max_degree=max_degree,
        )

    async def kg_single_source_shortest_path(
        self,
        source: str,
        *,
        direction: str = "OUT",
        weight_prop: str = "weight",
        max_degree: int = 10000,
    ) -> dict[str, Any]:
        """Single source shortest path to all reachable vertices."""
        self._ensure_kg_enabled()
        client = self._get_kg_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="KGClient is not available")
        return await client.traverser_single_source_shortest_path(
            source, direction=direction,
            weight_prop=weight_prop, max_degree=max_degree,
        )

    async def kg_multi_node_shortest_path(
        self,
        sources: list[str],
        targets: list[str],
        *,
        direction: str = "OUT",
        weight_prop: str = "weight",
        max_degree: int = 10000,
    ) -> list[dict[str, Any]]:
        """Shortest paths between multiple source-target pairs."""
        self._ensure_kg_enabled()
        client = self._get_kg_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="KGClient is not available")
        return await client.traverser_multi_node_shortest_path(
            sources, targets, direction=direction,
            weight_prop=weight_prop, max_degree=max_degree,
        )

    async def kg_rays(
        self,
        source: str,
        *,
        direction: str = "OUT",
        max_depth: int = 5,
    ) -> list[dict[str, Any]]:
        """Rays — non-cyclic paths from source."""
        self._ensure_kg_enabled()
        client = self._get_kg_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="KGClient is not available")
        return await client.traverser_rays(
            source, direction=direction, max_depth=max_depth,
        )

    async def kg_rings(
        self,
        source: str,
        *,
        direction: str = "OUT",
        max_depth: int = 5,
    ) -> list[dict[str, Any]]:
        """Ring detection — cyclic paths from source back to itself."""
        self._ensure_kg_enabled()
        client = self._get_kg_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="KGClient is not available")
        return await client.traverser_rings(
            source, direction=direction, max_depth=max_depth,
        )

    async def kg_crosspoints(
        self,
        source: str,
        target: str,
        *,
        direction: str = "OUT",
        max_depth: int = 5,
    ) -> list[dict[str, Any]]:
        """Crosspoints — vertices on paths between source and target."""
        self._ensure_kg_enabled()
        client = self._get_kg_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="KGClient is not available")
        return await client.traverser_crosspoints(
            source, target, direction=direction, max_depth=max_depth,
        )

    async def kg_customized_paths(
        self,
        source: str,
        steps: list[dict[str, Any]],
        *,
        with_vertex: bool = True,
        with_edge: bool = True,
    ) -> list[dict[str, Any]]:
        """Customized multi-step path traversal."""
        self._ensure_kg_enabled()
        client = self._get_kg_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="KGClient is not available")
        return await client.traverser_customized_paths(
            source, steps, with_vertex=with_vertex, with_edge=with_edge,
        )

    # ------------------------------------------------------------------
    # Graph Import / Export (2 methods)
    # ------------------------------------------------------------------

    async def kg_export_graph(self, *, with_properties: bool = True) -> dict[str, Any]:
        """Export full graph as JSON dict: {vertices: [...], edges: [...]}."""
        self._ensure_kg_enabled()
        client = self._get_kg_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="KGClient is not available")
        return await client.export_graph(with_properties=with_properties)

    async def kg_import_graph(self, data: dict[str, Any]) -> dict[str, Any]:
        """Import graph from JSON dict. Returns {vertices_added, edges_added}."""
        self._ensure_kg_enabled()
        client = self._get_kg_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="KGClient is not available")
        return await client.import_graph(data)

    # ------------------------------------------------------------------
    # Vermeer OLAP Algorithms (9 methods)
    # ------------------------------------------------------------------

    async def kg_pagerank(
        self,
        *,
        iterations: int = 20,
        damping_factor: float = 0.85,
    ) -> dict[str, Any]:
        """PageRank — identify important vertices via Vermeer OLAP."""
        self._ensure_kg_enabled()
        client = self._get_vermeer_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="VermeerClient is not available")
        return await client.pagerank(
            iterations=iterations, damping_factor=damping_factor,
        )

    async def kg_louvain(self, *, resolution: float = 1.0) -> dict[str, Any]:
        """Louvain community detection via Vermeer OLAP."""
        self._ensure_kg_enabled()
        client = self._get_vermeer_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="VermeerClient is not available")
        return await client.louvain(resolution=resolution)

    async def kg_label_propagation(self, **params: Any) -> dict[str, Any]:
        """Label Propagation community detection via Vermeer OLAP."""
        self._ensure_kg_enabled()
        client = self._get_vermeer_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="VermeerClient is not available")
        return await client.label_propagation(**params)

    async def kg_wcc(self) -> dict[str, Any]:
        """Weakly Connected Components via Vermeer OLAP."""
        self._ensure_kg_enabled()
        client = self._get_vermeer_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="VermeerClient is not available")
        return await client.wcc()

    async def kg_triangle_count(self) -> dict[str, Any]:
        """Triangle Counting via Vermeer OLAP."""
        self._ensure_kg_enabled()
        client = self._get_vermeer_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="VermeerClient is not available")
        return await client.triangle_count()

    async def kg_degree_centrality(self) -> dict[str, Any]:
        """Degree Centrality via Vermeer OLAP."""
        self._ensure_kg_enabled()
        client = self._get_vermeer_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="VermeerClient is not available")
        return await client.degree_centrality()

    async def kg_closeness_centrality(self) -> dict[str, Any]:
        """Closeness Centrality via Vermeer OLAP."""
        self._ensure_kg_enabled()
        client = self._get_vermeer_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="VermeerClient is not available")
        return await client.closeness_centrality()

    async def kg_k_core(self, *, k: int = 3) -> dict[str, Any]:
        """K-Core decomposition via Vermeer OLAP."""
        self._ensure_kg_enabled()
        client = self._get_vermeer_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="VermeerClient is not available")
        return await client.k_core(k=k)

    async def kg_betweenness_centrality(self) -> dict[str, Any]:
        """Betweenness Centrality via Vermeer OLAP."""
        self._ensure_kg_enabled()
        client = self._get_vermeer_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message="VermeerClient is not available")
        return await client.betweenness_centrality()
