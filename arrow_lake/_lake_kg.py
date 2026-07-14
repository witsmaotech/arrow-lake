"""Knowledge graph operations mixin for the Lake facade."""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from arrow_lake.exceptions import ErrorCode, KGError
from arrow_lake.knowledge_graph._naming import graph_name_for

if TYPE_CHECKING:
    from arrow_lake.knowledge_graph.builder import KGBuilder
    from arrow_lake.knowledge_graph.client import HugeGraphClient
    from arrow_lake.knowledge_graph.extractor import EntityExtractor
    from arrow_lake.knowledge_graph.retriever import KGRetriever
    from arrow_lake.knowledge_graph.vermeer_client import VermeerClient

logger = logging.getLogger(__name__)


def _scope_gremlin_to_graph(query: str, graph: str) -> str:
    """Rewrite a raw Gremlin query to target a specific graph.

    HugeGraph 1.7 binds one TraversalSource per graph as ``{graph}.traversal()``.
    Cookbook / verification queries use the default source ``g`` (e.g.
    ``g.V().groupCount().by(label)``), which reads the configured DEFAULT graph —
    not the per-dataset ``kg_{dataset}`` graph where ``kg_build`` writes. This
    rewrites a leading ``g.`` to ``{graph}.traversal().`` so the query hits the
    intended graph. Queries already scoped (``{name}.traversal()``) or using any
    other source are passed through unchanged.
    """
    import re

    return re.sub(r"^\s*g\.", f"{graph}.traversal().", query.lstrip())


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

    def _get_kg_embedder(self) -> Any:
        return self._get_component("kg_embedder", self._create_kg_embedder)

    def _create_kg_embedder(self) -> Any:
        """Build a langchain ``Embeddings`` over the project encoder (singleton).

        Mirrors :meth:`LakeIngestor.embed_and_add`'s three-branch construction
        (DAFT / OPENAI / LOCAL) over ``ArrowLakeConfig.embedding``, then wraps
        it in :class:`_LakeEmbedderAdapter` so hyper-extract's
        ``Template.create(embedder=...)`` can build a FAISS index. RAY_SERVE
        degrades to LOCAL. Lazy single instance via ``_get_component``.
        """
        from arrow_lake.config._enums import EmbeddingBackend
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder, LocalEmbeddingEncoder
        from arrow_lake.knowledge_graph._he_embedder import _LakeEmbedderAdapter

        cfg = self._config.embedding
        if cfg.backend == EmbeddingBackend.OPENAI and cfg.api_base:
            enc = ApiEmbeddingEncoder(
                api_base=cfg.api_base, api_key=cfg.api_key,
                model_name=cfg.model, batch_size=cfg.batch_size,
            )
        elif cfg.backend == EmbeddingBackend.DAFT:
            from arrow_lake.embed.daft_encoder import DaftBatchEncoder

            enc = DaftBatchEncoder(
                model=cfg.model, provider=cfg.daft_provider,
                num_partitions=cfg.daft_num_partitions, expected_dim=cfg.expected_dim,
            )
        else:  # LOCAL / RAY_SERVE (degrade to LOCAL)
            enc = LocalEmbeddingEncoder(
                model_name=cfg.model, batch_size=cfg.batch_size,
                expected_dim=cfg.expected_dim,
            )
        return _LakeEmbedderAdapter(enc)

    def _create_kg_extractor(self) -> Any:
        from arrow_lake.rag.provider import create_llm_provider

        hg = self._config.hugegraph
        if hg.extractor_backend == "he":
            from arrow_lake.knowledge_graph.doc_type_router import (
                DocTypeClassifier,
                DocTypeRouter,
            )
            from arrow_lake.knowledge_graph.he_extractor import HyperExtractExtractor

            # P3: classifier infers doc_type from content when the caller omits
            # it (best-effort, only fires on doc_type=None). Built from the same
            # LLM config; degrades to no-op if construction fails.
            try:
                classifier = DocTypeClassifier.from_llm_config(self._config.llm)
            except Exception as exc:
                logger.warning("doc_type classifier disabled: %s", exc)
                classifier = None

            return HyperExtractExtractor(
                self._config.llm,
                doc_type_router=DocTypeRouter(
                    hg.he_doc_type_templates, hg.he_default_template
                ),
                language=hg.he_language,
                model=hg.he_model,
                doc_type_classifier=classifier,
                embedder=self._get_kg_embedder(),
                kg_granularity=hg.he_kg_granularity,
                hugegraph_config=hg,
            )
        from arrow_lake.knowledge_graph.extractor import EntityExtractor

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
        return KGBuilder(
            client,
            extractor,
            self._config.hugegraph,
            ka_base_dir=self._config.hugegraph.he_ka_base_dir,
        )

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

    def _dataset_graph(self, dataset_name: str) -> str:
        """Map a lake path (dataset name) to its isolated HugeGraph name."""
        return graph_name_for(dataset_name)

    @contextmanager
    def _require_kg_client(self, label: str = "KGClient"):
        """Context manager: ensure KG enabled and yield the client."""
        self._ensure_kg_enabled()
        client = self._get_kg_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message=f"{label} is not available")
        yield client

    @contextmanager
    def _require_kg_builder(self, label: str = "KGBuilder"):
        """Context manager: ensure KG enabled and yield the builder."""
        self._ensure_kg_enabled()
        builder = self._get_kg_builder()
        if builder is None:
            raise KGError(error_code=ErrorCode.KG_BUILD_FAILED, message=f"{label} is not available")
        yield builder

    @contextmanager
    def _require_vermeer_client(self, label: str = "VermeerClient"):
        """Context manager: ensure KG enabled and yield the Vermeer client."""
        self._ensure_kg_enabled()
        client = self._get_vermeer_client()
        if client is None:
            raise KGError(error_code=ErrorCode.KG_QUERY_FAILED, message=f"{label} is not available")
        yield client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def kg_build(self, dataset_name: str) -> str:
        """Build a knowledge graph from a dataset.

        Reads text chunks from the specified dataset, extracts entities
        and relations via LLM, and inserts them into HugeGraph.

        The data preparation (load + normalize) runs in a thread executor
        so it never blocks the uvicorn event loop.  The actual KG build
        is fire-and-forget via ``asyncio.create_task``.

        Args:
            dataset_name: Name of the Lance dataset to build KG from.

        Returns:
            Task ID string for tracking build progress.

        Raises:
            KGError: If KG is not enabled or build fails.
        """
        self._ensure_kg_enabled()

        with self._require_kg_builder() as builder:
            # Sync I/O (LanceDB read + Arrow normalize) in thread pool
            # to avoid blocking the uvicorn event loop.
            table = await asyncio.get_running_loop().run_in_executor(
                None, self._load_kg_table, dataset_name,
            )

            task_id = await builder.build(dataset_name, table)

            # Fire-and-forget via TaskManager for consistent status tracking.
            # TaskManager.run_background handles both sync and async callables
            # and keeps the task status in the same process as the handler,
            # which avoids the multi-worker state-split issue for the originating
            # worker.
            from arrow_lake.api.tasks import TaskManager

            tm_task_id = TaskManager.create_task(
                "kg_build", dataset_name, detail={"kg_task_id": task_id},
            )

            async def _run_build() -> None:
                await TaskManager.run_background(tm_task_id, builder.execute_build, task_id)
                # Sync final status from KGBuilder task into TaskManager
                kg_task = builder.get_task_status(task_id)
                tm_task = TaskManager.get_task(tm_task_id)
                if kg_task and tm_task:
                    tm_task.progress = kg_task.processed_chunks / max(kg_task.total_chunks, 1)
                    if kg_task.entity_count or kg_task.relation_count:
                        tm_task.detail = {
                            "entity_count": kg_task.entity_count,
                            "relation_count": kg_task.relation_count,
                        }
                    # Sync updated state to Redis for cross-worker visibility
                    TaskManager._sync_to_redis(tm_task)

            asyncio.create_task(_run_build())  # noqa: RUF006
            return task_id

    def _load_kg_table(self, dataset_name: str):
        """Synchronous helper: load and normalize a Lance table for KG build."""
        import pyarrow as pa

        storage = self._get_storage()
        dataset = storage.open_dataset(dataset_name)
        table = dataset.search().to_arrow()

        # Normalize required columns (builder also does this as safety net)
        if "content" not in table.column_names:
            text_col = (
                "text_content"
                if "text_content" in table.column_names
                else table.column_names[0]
            )
            new_names = ["content" if c == text_col else c for c in table.column_names]
            table = table.rename_columns(new_names)
        if "id" not in table.column_names:
            table = table.add_column(
                0, "id", pa.array([str(i) for i in range(table.num_rows)])
            )
        if "document_name" not in table.column_names:
            table = table.append_column(
                "document_name", pa.array([dataset_name] * table.num_rows)
            )
        if "chunk_index" not in table.column_names:
            table = table.append_column(
                "chunk_index", pa.array(list(range(table.num_rows)))
            )
        return table

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
        dataset_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a Gremlin query against the knowledge graph.

        Args:
            query: Gremlin query string.
            traversal_depth: Optional traversal depth limit.
            dataset_name: Optional lake dataset — when set, a leading ``g.``
                traversal source is rewritten to ``kg_{dataset}.traversal()``
                so the query reads the per-dataset graph (where ``kg_build``
                writes) instead of the configured default graph. Without this,
                a bare ``g.V()`` reads the default graph and silently returns
                empty / stale results on isolated per-dataset deployments.

                Note: HugeGraph 1.7.0 does NOT auto-bind dynamically-created
                graphs as Gremlin traversal sources, so a per-dataset raw
                Gremlin query raises ``MissingPropertyException`` until the
                graph is bound server-side. For per-dataset reads prefer the
                REST-backed methods (``kg_stats``, ``kg_find_entities``,
                ``kg_get_neighbors``) which are graph-scoped and work without
                binding. ``kg_query`` is the power-user Gremlin escape hatch.

        Returns:
            List of query result dicts.

        Raises:
            KGError: If KG is not enabled or query fails.
        """
        with self._require_kg_client() as client:
            if dataset_name:
                query = _scope_gremlin_to_graph(query, graph_name_for(dataset_name))
            return await client.gremlin(query)

    async def kg_get_neighbors(
        self,
        entity_id: str,
        depth: int = 1,
        dataset_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get neighbor vertices of a given entity.

        Args:
            entity_id: Vertex ID to start traversal from.
            depth: Traversal depth (number of hops).
            dataset_name: Optional lake path — scopes traversal to ``kg_{ds}``.
                When omitted, the configured default graph is used.

        Returns:
            List of neighbor vertex dicts.

        Raises:
            KGError: If KG is not enabled or traversal fails.
        """
        with self._require_kg_client() as client:
            depth = min(depth, self._config.hugegraph.max_traversal_depth)
            g = graph_name_for(dataset_name) if dataset_name else None
            return await client.traverser_kneighbor(
                source=entity_id, depth=depth, graph_name=g
            )

    async def kg_stats(self, dataset_name: str | None = None) -> dict[str, Any]:
        """Get knowledge graph statistics.

        Args:
            dataset_name: Optional lake path — scopes counts to ``kg_{ds}``.
                When omitted, the configured default graph is used.

        Returns:
            Dict with vertex and edge counts.

        Raises:
            KGError: If KG is not enabled.
        """
        with self._require_kg_client() as client:
            g = graph_name_for(dataset_name) if dataset_name else None
            return await client.get_stats(graph_name=g)

    # ------------------------------------------------------------------
    # doc_type / template metadata (v1.8.8) — pure metadata, no KG client
    # ------------------------------------------------------------------

    async def kg_list_doc_types(self) -> list[dict[str, Any]]:
        """List the canonical doc_types with aliases, description, and the
        template they auto-resolve to.

        Read-only metadata (shared hyper-extract template gallery + doc_type
        taxonomy); does NOT require HugeGraph. Use it to discover the right
        ``doc_type`` to pass ``ingest_documents`` and bypass the classifier.
        """
        from arrow_lake.knowledge_graph.doc_type_router import (
            DOC_TYPE_ALIASES,
            DOC_TYPE_DESCRIPTIONS,
            DocTypeRouter,
        )

        hg = self._config.hugegraph
        router = DocTypeRouter(hg.he_doc_type_templates, hg.he_default_template)
        out: list[dict[str, Any]] = []
        for doc_type in DOC_TYPE_DESCRIPTIONS:  # canonical order
            path, source = router.resolve_with_source(doc_type)
            out.append(
                {
                    "doc_type": doc_type,
                    "description": DOC_TYPE_DESCRIPTIONS[doc_type],
                    "aliases": list(DOC_TYPE_ALIASES.get(doc_type, ())),
                    "resolved_template": path,
                    "resolution": source,
                }
            )
        return out

    async def kg_list_templates(self, category: str | None = None) -> list[dict[str, Any]]:
        """List hyper-extract preset templates (optionally filtered by category).

        Each entry is a :meth:`TemplateInfo.to_summary` dict. Read-only; does
        not require HugeGraph.
        """
        from arrow_lake.knowledge_graph.doc_type_router import get_template_gallery

        templates = get_template_gallery().templates
        if category:
            cat = category.strip().lower()
            templates = [t for t in templates if t.category == cat]
        return [t.to_summary() for t in templates]

    async def kg_describe_template(self, path: str) -> dict[str, Any]:
        """Return the full detail for template ``path`` (e.g.
        ``general/concept_graph``).

        Raises:
            KGError: If the template is not found (``KG_GRAPH_NOT_FOUND`` → HTTP 404).
        """
        from arrow_lake.knowledge_graph.doc_type_router import get_template_gallery

        detail = get_template_gallery().describe(path)
        if detail is None:
            raise KGError(
                error_code=ErrorCode.KG_GRAPH_NOT_FOUND,
                message=f"hyper-extract template not found: {path!r}",
            )
        return detail

    async def kg_graph_exists(self, dataset_name: str | None = None) -> bool:
        """Check if the configured HugeGraph graph space exists.

        Args:
            dataset_name: Optional lake path — checks ``kg_{ds}`` instead of
                the configured default graph.

        Returns:
            True if graph exists, False otherwise.
        """
        client = self._get_kg_client()
        if client is None:
            return False
        try:
            g = graph_name_for(dataset_name) if dataset_name else None
            return await client.graph_exists(graph_name=g)
        except Exception:
            return False

    async def kg_ensure_graph(self, dataset_name: str | None = None) -> bool:
        """Ensure the HugeGraph graph space exists, creating if needed.

        Args:
            dataset_name: Optional lake path — ensures ``kg_{ds}``.

        Returns:
            True if graph was confirmed to exist (pre-existing or newly created).
        """
        client = self._get_kg_client()
        if client is None:
            return False
        try:
            g = graph_name_for(dataset_name) if dataset_name else None
            exists = await client.graph_exists(graph_name=g)
            if exists:
                return True
            return await client.ensure_graph(graph_name=g)
        except Exception:
            return False

    async def kg_delete_graph(self, dataset_name: str | None = None) -> None:
        """Delete all data from the knowledge graph (clears data, keeps shell).

        Args:
            dataset_name: Optional lake path — clears ``kg_{ds}``.

        Use with caution -- this operation is irreversible.

        Raises:
            KGError: If KG is not enabled or deletion fails.
        """
        with self._require_kg_client() as client:
            g = graph_name_for(dataset_name) if dataset_name else None
            await client.clear(graph_name=g)

    async def kg_drop_graph(self, dataset_name: str) -> None:
        """Drop a dataset's isolated graph entirely (data + schema + shell).

        Idempotent: a missing graph is logged and not an error. Used for
        drop-on-dataset-delete (wired into ``Lake.delete_dataset``).

        Args:
            dataset_name: Lake path whose ``kg_{ds}`` graph should be dropped.

        Raises:
            KGError: If KG is not enabled.
        """
        self._ensure_kg_enabled()
        client = self._get_kg_client()
        if client is None:
            return
        g = graph_name_for(dataset_name)
        try:
            await client.drop_graph(g)
            logger.info("Dropped graph '%s' for dataset '%s'", g, dataset_name)
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            logger.warning(
                "Drop graph '%s' failed (best-effort): %s", g, exc
            )

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
        dataset_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """All shortest paths between source and target vertices."""
        g = graph_name_for(dataset_name) if dataset_name else None
        with self._require_kg_client() as client:
            return await client.traverser_all_shortest_paths(
                source, target, direction=direction, max_depth=max_depth, graph_name=g,
            )

    async def kg_weighted_shortest_path(
        self,
        source: str,
        target: str,
        *,
        direction: str = "OUT",
        weight_prop: str = "weight",
        max_degree: int = 10000,
        dataset_name: str | None = None,
    ) -> dict[str, Any]:
        """Weighted shortest path between source and target."""
        g = graph_name_for(dataset_name) if dataset_name else None
        with self._require_kg_client() as client:
            return await client.traverser_weighted_shortest_path(
                source, target, direction=direction,
                weight_prop=weight_prop, max_degree=max_degree, graph_name=g,
            )

    async def kg_single_source_shortest_path(
        self,
        source: str,
        *,
        direction: str = "OUT",
        weight_prop: str = "weight",
        max_degree: int = 10000,
        dataset_name: str | None = None,
    ) -> dict[str, Any]:
        """Single source shortest path to all reachable vertices."""
        g = graph_name_for(dataset_name) if dataset_name else None
        with self._require_kg_client() as client:
            return await client.traverser_single_source_shortest_path(
                source, direction=direction,
                weight_prop=weight_prop, max_degree=max_degree, graph_name=g,
            )

    async def kg_multi_node_shortest_path(
        self,
        sources: list[str],
        targets: list[str],
        *,
        direction: str = "OUT",
        weight_prop: str = "weight",
        max_degree: int = 10000,
        dataset_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Shortest paths between multiple source-target pairs."""
        g = graph_name_for(dataset_name) if dataset_name else None
        with self._require_kg_client() as client:
            return await client.traverser_multi_node_shortest_path(
                sources, targets, direction=direction,
                weight_prop=weight_prop, max_degree=max_degree, graph_name=g,
            )

    async def kg_rays(
        self,
        source: str,
        *,
        direction: str = "OUT",
        max_depth: int = 5,
        dataset_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Rays — non-cyclic paths from source."""
        g = graph_name_for(dataset_name) if dataset_name else None
        with self._require_kg_client() as client:
            return await client.traverser_rays(
                source, direction=direction, max_depth=max_depth, graph_name=g,
            )

    async def kg_rings(
        self,
        source: str,
        *,
        direction: str = "OUT",
        max_depth: int = 5,
        dataset_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Ring detection — cyclic paths from source back to itself."""
        g = graph_name_for(dataset_name) if dataset_name else None
        with self._require_kg_client() as client:
            return await client.traverser_rings(
                source, direction=direction, max_depth=max_depth, graph_name=g,
            )

    async def kg_crosspoints(
        self,
        source: str,
        target: str,
        *,
        direction: str = "OUT",
        max_depth: int = 5,
        dataset_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Crosspoints — vertices on paths between source and target."""
        g = graph_name_for(dataset_name) if dataset_name else None
        with self._require_kg_client() as client:
            return await client.traverser_crosspoints(
                source, target, direction=direction, max_depth=max_depth, graph_name=g,
            )

    async def kg_customized_paths(
        self,
        source: str,
        steps: list[dict[str, Any]],
        *,
        with_vertex: bool = True,
        with_edge: bool = True,
        dataset_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Customized multi-step path traversal."""
        g = graph_name_for(dataset_name) if dataset_name else None
        with self._require_kg_client() as client:
            return await client.traverser_customized_paths(
                source, steps, with_vertex=with_vertex, with_edge=with_edge, graph_name=g,
            )

    # ------------------------------------------------------------------
    # Graph Import / Export (2 methods)
    # ------------------------------------------------------------------

    async def kg_export_graph(self, *, with_properties: bool = True) -> dict[str, Any]:
        """Export full graph as JSON dict: {vertices: [...], edges: [...]}."""
        with self._require_kg_client() as client:
            return await client.export_graph(with_properties=with_properties)

    async def kg_import_graph(self, data: dict[str, Any]) -> dict[str, Any]:
        """Import graph from JSON dict. Returns {vertices_added, edges_added}."""
        with self._require_kg_client() as client:
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
        with self._require_vermeer_client() as client:
            return await client.pagerank(
                iterations=iterations, damping_factor=damping_factor,
            )

    async def kg_louvain(self, *, resolution: float = 1.0) -> dict[str, Any]:
        """Louvain community detection via Vermeer OLAP."""
        with self._require_vermeer_client() as client:
            return await client.louvain(resolution=resolution)

    async def kg_label_propagation(self, **params: Any) -> dict[str, Any]:
        """Label Propagation community detection via Vermeer OLAP."""
        with self._require_vermeer_client() as client:
            return await client.label_propagation(**params)

    async def kg_wcc(self) -> dict[str, Any]:
        """Weakly Connected Components via Vermeer OLAP."""
        with self._require_vermeer_client() as client:
            return await client.wcc()

    async def kg_triangle_count(self) -> dict[str, Any]:
        """Triangle Counting via Vermeer OLAP."""
        with self._require_vermeer_client() as client:
            return await client.triangle_count()

    async def kg_degree_centrality(self) -> dict[str, Any]:
        """Degree Centrality via Vermeer OLAP."""
        with self._require_vermeer_client() as client:
            return await client.degree_centrality()

    async def kg_closeness_centrality(self) -> dict[str, Any]:
        """Closeness Centrality via Vermeer OLAP."""
        with self._require_vermeer_client() as client:
            return await client.closeness_centrality()

    async def kg_k_core(self, *, k: int = 3) -> dict[str, Any]:
        """K-Core decomposition via Vermeer OLAP."""
        with self._require_vermeer_client() as client:
            return await client.k_core(k=k)

    async def kg_betweenness_centrality(self) -> dict[str, Any]:
        """Betweenness Centrality via Vermeer OLAP."""
        with self._require_vermeer_client() as client:
            return await client.betweenness_centrality()
