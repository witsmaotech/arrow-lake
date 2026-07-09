"""Search mixin — vector search, text search, hybrid, faceted, ensemble."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from arrow_lake.query.ensemble import EnsembleSearchResult
    from arrow_lake.query.faceted import FacetedSearchResult
    from arrow_lake.query.fts import FullTextSearchResult
    from arrow_lake.query.hybrid import HybridSearchResult
    from arrow_lake.query.vector import IndexInfo, VectorSearchResult


class _LakeSearchMixin:
    """Provides vector search, text search, hybrid search, faceted, and ensemble."""

    def _bridge_kwargs(self) -> dict[str, Any]:
        """Common kwargs for all search bridge factories."""
        return {
            "storage_config": self._config.storage,
            "lance_scan_mode": self._config.olap.lance_scan_mode,
            "session_manager": self.get_session_manager(),
        }

    def search(
        self,
        dataset_name: str,
        query_vector: list[float],
        *,
        top_k: int = 10,
        metric: str | None = None,
        vector_column: str = "text_embedding",
        where: str | None = None,
        nprobes: int | None = None,
        version: int | None = None,
    ) -> VectorSearchResult:
        """Vector similarity search across ingested data (Story 5.1).

        Delegates to VectorSearchBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            query_vector: Query embedding vector.
            top_k: Number of results to return.
            metric: Distance metric ('cosine', 'l2', 'dot').
            vector_column: Name of the vector column.
            where: Optional metadata filter expression.
            nprobes: Number of IVF partitions to probe.
            version: Dataset version for time-travel query (None = latest).

        Returns:
            VectorSearchResult with Arrow table and distance scores.
        """
        from arrow_lake.query.vector import VectorSearchBridge

        bridge = self._get_component(
            "vector",
            lambda: VectorSearchBridge(
                self._get_storage(),
                config=self._config.vector,
                **self._bridge_kwargs(),
            ),
        )
        from arrow_lake.api.telemetry import get_tracer
        from arrow_lake.core.metrics import _QueryTimer

        with get_tracer().start_as_current_span("vector_search", attributes={"dataset": dataset_name}), _QueryTimer("vector_search"):
            return bridge.search(
                dataset_name,
                query_vector,
                top_k=top_k,
                metric=metric,
                vector_column=vector_column,
                where=where,
                nprobes=nprobes,
                version=version,
            )

    def create_vector_index(
        self,
        dataset_name: str,
        *,
        metric: str = "",
        vector_column: str = "text_embedding",
        index_type: str = "",
        num_partitions: int | None = None,
        num_sub_vectors: int | None = None,
        replace: bool = True,
    ) -> IndexInfo:
        """Create a vector index on a dataset (Story 5.1).

        Delegates to VectorSearchBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            metric: Distance metric (None = use config).
            vector_column: Name of the vector column.
            index_type: LanceDB index type (None = use config).
            num_partitions: IVF partitions (None = auto).
            num_sub_vectors: PQ sub-vectors (0 = use config).
            replace: Whether to replace existing index.

        Returns:
            IndexInfo with index metadata.
        """
        from arrow_lake.query.vector import VectorSearchBridge

        bridge = self._get_component(
            "vector",
            lambda: VectorSearchBridge(
                self._get_storage(),
                config=self._config.vector,
                **self._bridge_kwargs(),
            ),
        )
        return bridge.create_index(
            dataset_name,
            metric=metric,
            vector_column=vector_column,
            index_type=index_type,
            num_partitions=num_partitions,
            num_sub_vectors=num_sub_vectors,
            replace=replace,
        )

    async def search_async(
        self,
        dataset_name: str,
        query_vector: list[float],
        *,
        top_k: int | None = None,
        vector_column: str = "text_embedding",
        where: str | None = None,
        nprobes: int | None = None,
    ) -> Any:
        """Async vector search (v1.7.1 #9). Delegates to VectorSearchBridge.search_async.

        Async entry point for high-concurrency workloads. Connection/table
        handles are pooled process-wide (v1.8.x #1, ``async_conn_pool.py``);
        load-test before relying on it for peak throughput.
        """
        from arrow_lake.query.vector import VectorSearchBridge

        bridge = self._get_component(
            "vector",
            lambda: VectorSearchBridge(
                self._get_storage(),
                config=self._config.vector,
                **self._bridge_kwargs(),
            ),
        )
        return await bridge.search_async(
            dataset_name,
            query_vector,
            top_k=top_k,
            vector_column=vector_column,
            where=where,
            nprobes=nprobes,
        )

    def encode_text_clip(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        model_source: str = "huggingface",
    ) -> list[list[float]]:
        """Encode texts via CLIP/SigLIP text tower for cross-modal retrieval (v1.8.0 #6).

        Convenience facade for :meth:`CLIPImageEncoder.encode_text` — produces
        text embeddings in the same space as image embeddings, so a text query
        can retrieve images::

            q = lake.encode_text_clip(["a cat"])[0]
            lake.search("photos", q, vector_column="image_embedding")

        Args:
            texts: Text strings to encode.
            model: CLIP/SigLIP model id (None = use embedding config default).
            model_source: "huggingface" or "modelscope".

        Returns:
            List of L2-normalized embedding vectors (one per input text).
        """
        from arrow_lake.embed.image_encoder import CLIPImageEncoder

        emb_cfg = self._config.embedding
        encoder = CLIPImageEncoder(
            model_name=model or emb_cfg.model,
            model_source=model_source,
        )
        return encoder.encode_text(list(texts)).tolist()

    def text_search(
        self,
        dataset_name: str,
        query: str,
        *,
        top_k: int | None = None,
        fts_column: str | None = None,
        where: str | None = None,
        version: int | None = None,
        offset: int = 0,
    ) -> FullTextSearchResult:
        """Full-text search over ingested data (Story 5.2).

        Delegates to FullTextSearchBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            query: Search query string.
            top_k: Number of results (None = use config default).
            fts_column: Text column to search (None = use config default).
            where: Optional metadata filter expression.
            offset: Number of results to skip for pagination.

        Returns:
            FullTextSearchResult with Arrow table and _score relevance.
        """
        from arrow_lake.query.fts import FullTextSearchBridge

        bridge = self._get_component(
            "fts",
            lambda: FullTextSearchBridge(
                self._get_storage(),
                config=self._config.fts,
                **self._bridge_kwargs(),
            ),
        )
        from arrow_lake.api.telemetry import get_tracer
        from arrow_lake.core.metrics import _QueryTimer

        tracer = get_tracer()
        with tracer.start_as_current_span("text_search", attributes={"dataset": dataset_name}), _QueryTimer("text_search"):
            return bridge.search(
                dataset_name,
                query,
                top_k=top_k,
                fts_column=fts_column,
                where=where,
                version=version,
                offset=offset,
            )

    async def text_search_async(
        self,
        dataset_name: str,
        query: str,
        *,
        top_k: int | None = None,
        fts_column: str | None = None,
        where: str | None = None,
        version: int | None = None,
        offset: int = 0,
    ) -> FullTextSearchResult:
        """Async full-text search (v1.8.0 #17).

        Delegates to :meth:`FullTextSearchBridge.search_async` — a non-blocking
        wrapper (``asyncio.to_thread``) since lancedb has no native async FTS
        path. Keeps the event loop responsive under concurrent async handlers.
        Same params/return as :meth:`text_search`.
        """
        from arrow_lake.query.fts import FullTextSearchBridge

        bridge = self._get_component(
            "fts",
            lambda: FullTextSearchBridge(
                self._get_storage(),
                config=self._config.fts,
                **self._bridge_kwargs(),
            ),
        )
        from arrow_lake.api.telemetry import get_tracer
        from arrow_lake.core.metrics import _QueryTimer

        with get_tracer().start_as_current_span(
            "text_search_async", attributes={"dataset": dataset_name}
        ), _QueryTimer("text_search_async"):
            return await bridge.search_async(
                dataset_name,
                query,
                top_k=top_k,
                fts_column=fts_column,
                where=where,
                version=version,
                offset=offset,
            )

    def create_fts_index(
        self,
        dataset_name: str,
        *,
        fts_column: str | None = None,
        replace: bool = True,
    ) -> None:
        """Create a full-text search index on a dataset (Story 5.2).

        Delegates to FullTextSearchBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            fts_column: Text column to index (None = use config default).
            replace: Whether to replace existing index.
        """
        from arrow_lake.query.fts import FullTextSearchBridge

        bridge = self._get_component(
            "fts",
            lambda: FullTextSearchBridge(
                self._get_storage(),
                config=self._config.fts,
                **self._bridge_kwargs(),
            ),
        )
        bridge.create_index(dataset_name, fts_column=fts_column, replace=replace)

    def create_scalar_index(
        self,
        dataset_name: str,
        *,
        column: str,
        index_type: str = "BTREE",
        replace: bool = True,
        index_name: str | None = None,
    ) -> None:
        """Create a scalar index on a column (v1.7.1 #3).

        Delegates to storage. Low-cardinality columns (modality/source/doc_type)
        benefit from BITMAP; ordered/numeric (created_at/quality_score) from BTREE.

        Args:
            dataset_name: Name of the Lance dataset.
            column: Column to index.
            index_type: Scalar index type (BTREE/BITMAP/ZONEMAP/...).
            replace: Overwrite existing index on this column.
            index_name: Optional explicit index name.
        """
        self._get_storage().create_scalar_index(
            dataset_name,
            column,
            index_type=index_type,
            replace=replace,
            index_name=index_name,
        )

    def create_facet_indexes(
        self,
        dataset_name: str,
        columns: list[str] | None = None,
    ) -> dict[str, str]:
        """Create scalar indexes on facet columns in bulk (v1.7.1 #3).

        Defaults to FacetedSearchConfig.facet_filter_columns + scalar_index_type_map.

        Args:
            dataset_name: Name of the Lance dataset.
            columns: Columns to index (None = config facet columns).

        Returns:
            Mapping of column → status ("created"|"skipped"|"failed").
        """
        if columns is None:
            columns = list(self._config.faceted.facet_filter_columns)
        type_map = dict(self._config.faceted.scalar_index_type_map)
        return self._get_storage().create_facet_indexes(
            dataset_name, columns, type_map=type_map
        )

    def hybrid_search(
        self,
        dataset_name: str,
        query_vector: list[float],
        query_text: str,
        *,
        top_k: int | None = None,
        vector_column: str = "text_embedding",
        fts_column: str | None = None,
        where: str | None = None,
        version: int | None = None,
    ) -> HybridSearchResult:
        """Hybrid search combining vector similarity + full-text via RRF (Story 5.3).

        Delegates to HybridSearchBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            query_vector: Query embedding vector.
            query_text: Search query string for FTS.
            top_k: Number of results (None = use config default).
            vector_column: Name of the vector column.
            fts_column: Text column to search (None = use config default).
            where: Optional metadata filter expression.

        Returns:
            HybridSearchResult with Arrow table and _rrf_score.
        """
        from arrow_lake.query.hybrid import HybridSearchBridge

        bridge = self._get_component(
            "hybrid",
            lambda: HybridSearchBridge(
                self._get_storage(),
                config=self._config.hybrid,
                **self._bridge_kwargs(),
            ),
        )
        from arrow_lake.api.telemetry import get_tracer
        from arrow_lake.core.metrics import _QueryTimer

        with get_tracer().start_as_current_span("hybrid_search", attributes={"dataset": dataset_name}), _QueryTimer("hybrid_search"):
            return bridge.search(
                dataset_name,
                query_vector,
                query_text,
                top_k=top_k,
                vector_column=vector_column,
                fts_column=fts_column,
                where=where,
                version=version,
            )

    async def hybrid_search_async(
        self,
        dataset_name: str,
        query_vector: list[float],
        query_text: str,
        *,
        top_k: int | None = None,
        vector_column: str = "text_embedding",
        fts_column: str | None = None,
        where: str | None = None,
        version: int | None = None,
    ) -> HybridSearchResult:
        """Async hybrid search (v1.8.0 #17).

        Delegates to :meth:`HybridSearchBridge.search_async` — non-blocking
        wrapper for the RRF + rerank pass. Same params/return as
        :meth:`hybrid_search`.
        """
        from arrow_lake.query.hybrid import HybridSearchBridge

        bridge = self._get_component(
            "hybrid",
            lambda: HybridSearchBridge(
                self._get_storage(),
                config=self._config.hybrid,
                **self._bridge_kwargs(),
            ),
        )
        from arrow_lake.api.telemetry import get_tracer
        from arrow_lake.core.metrics import _QueryTimer

        with get_tracer().start_as_current_span(
            "hybrid_search_async", attributes={"dataset": dataset_name}
        ), _QueryTimer("hybrid_search_async"):
            return await bridge.search_async(
                dataset_name,
                query_vector,
                query_text,
                top_k=top_k,
                vector_column=vector_column,
                fts_column=fts_column,
                where=where,
                version=version,
            )

    def faceted_search(
        self,
        dataset_name: str,
        query_vector: list[float],
        *,
        facets: list[str] | None = None,
        top_k: int = 10,
        vector_column: str = "embedding",
        where: str | None = None,
        version: int | None = None,
    ) -> FacetedSearchResult:
        """Faceted search combining facet counts with vector results (Story 8.1).

        Delegates to FacetedSearchBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            query_vector: Query embedding vector.
            facets: Column names for CUBE facet computation.
            top_k: Number of results.
            vector_column: Name of the vector column.
            where: Optional metadata filter.

        Returns:
            FacetedSearchResult with search results and facet counts.
        """
        from arrow_lake.query.faceted import FacetedSearchBridge

        bridge = self._get_component(
            "faceted",
            lambda: FacetedSearchBridge(
                self._get_storage(),
                config=self._config.faceted,
                storage_config=self._config.storage,
                session_manager=self.get_session_manager(),
            ),
        )
        from arrow_lake.core.metrics import _QueryTimer

        with _QueryTimer("faceted_search"):
            return bridge.search(
                dataset_name,
                query_vector,
                facets=facets,
                top_k=top_k,
                vector_column=vector_column,
                where=where,
                version=version,
            )

    async def faceted_search_async(
        self,
        dataset_name: str,
        query_vector: list[float],
        *,
        facets: list[str] | None = None,
        top_k: int = 10,
        vector_column: str = "embedding",
        where: str | None = None,
        version: int | None = None,
    ) -> FacetedSearchResult:
        """Async faceted search (v1.8.0 #17).

        Delegates to :meth:`FacetedSearchBridge.search_async` — non-blocking
        wrapper for the DuckDB CUBE aggregation. Same params/return as
        :meth:`faceted_search`.
        """
        from arrow_lake.query.faceted import FacetedSearchBridge

        bridge = self._get_component(
            "faceted",
            lambda: FacetedSearchBridge(
                self._get_storage(),
                config=self._config.faceted,
                storage_config=self._config.storage,
                session_manager=self.get_session_manager(),
            ),
        )
        from arrow_lake.core.metrics import _QueryTimer

        with _QueryTimer("faceted_search_async"):
            return await bridge.search_async(
                dataset_name,
                query_vector,
                facets=facets,
                top_k=top_k,
                vector_column=vector_column,
                where=where,
                version=version,
            )

    def ensemble_search(
        self,
        dataset_name: str,
        query_vector: list[float],
        *,
        columns: list[str] | None = None,
        weights: dict[str, float] | None = None,
        top_k: int | None = None,
        where: str | None = None,
        version: int | None = None,
    ) -> EnsembleSearchResult:
        """Multi-model ensemble search via weighted RRF fusion (Story 8.2).

        Searches multiple embedding columns and fuses results using
        weighted Reciprocal Rank Fusion.

        Args:
            dataset_name: Name of the Lance dataset.
            query_vector: Query embedding vector.
            columns: Embedding columns to search (None = auto-detect).
            weights: Per-column weights for RRF (None = all 1.0).
            top_k: Number of results.
            where: Optional metadata filter.

        Returns:
            EnsembleSearchResult with fused search results.
        """
        from arrow_lake.query.ensemble import EnsembleSearchBridge

        bridge = self._get_component(
            "ensemble",
            lambda: EnsembleSearchBridge(self._get_storage(), config=self._config.ensemble),
        )
        from arrow_lake.core.metrics import _QueryTimer

        with _QueryTimer("ensemble_search"):
            return bridge.search(
            dataset_name,
            query_vector,
            columns=columns,
            weights=weights,
            top_k=top_k,
            where=where,
            version=version,
        )

    def delete_vector_index(
        self,
        dataset_name: str,
        index_name: str,
    ) -> None:
        """Delete a vector index from a dataset.

        Args:
            dataset_name: Name of the Lance dataset.
            index_name: Name of the index to delete.

        Raises:
            StorageError: If dataset or index not found.
        """
        self._get_storage().delete_vector_index(dataset_name, index_name)

    def list_vector_indexes(self, dataset_name: str) -> list[IndexInfo]:
        """List all vector indexes on a dataset.

        Args:
            dataset_name: Name of the Lance dataset.

        Returns:
            List of IndexInfo for all vector indexes found.
        """
        from arrow_lake.query.vector import VectorSearchBridge

        self._get_component(
            "vector",
            lambda: VectorSearchBridge(
                self._get_storage(),
                config=self._config.vector,
                **self._bridge_kwargs(),
            ),
        )
        table = self._get_storage().open_dataset(dataset_name)
        results: list[IndexInfo] = []
        try:
            indices = list(table.list_indices())
            for idx_config in indices:
                cols = idx_config.columns if hasattr(idx_config, "columns") else []
                for col in cols:
                    info = VectorSearchBridge._get_latest_index_info(table, col)
                    if info is not None and info not in results:
                        results.append(info)
        except (ValueError, RuntimeError, OSError):
            pass
        return results

    def get_vector_index_info(
        self,
        dataset_name: str,
        vector_column: str | None = None,
    ) -> IndexInfo | None:
        """Get information about the vector index on a dataset.

        Args:
            dataset_name: Name of the Lance dataset.
            vector_column: Column to check (None = check default columns).

        Returns:
            IndexInfo if an index exists, None otherwise.
        """
        from arrow_lake.query.vector import VectorSearchBridge

        bridge = self._get_component(
            "vector",
            lambda: VectorSearchBridge(
                self._get_storage(),
                config=self._config.vector,
                **self._bridge_kwargs(),
            ),
        )
        return bridge.get_index_info(dataset_name, vector_column=vector_column)

    def rebuild_vector_index(
        self,
        dataset_name: str,
        *,
        metric: str = "",
        vector_column: str = "text_embedding",
        index_type: str = "",
        num_partitions: int | None = None,
        num_sub_vectors: int | None = None,
    ) -> IndexInfo:
        """Rebuild a vector index on a dataset.

        Drops the existing index and creates a new one.

        Args:
            dataset_name: Name of the Lance dataset.
            metric: Distance metric (empty = use config).
            vector_column: Name of the vector column.
            index_type: LanceDB index type (empty = use config).
            num_partitions: IVF partitions (None = auto).
            num_sub_vectors: PQ sub-vectors (None = auto).

        Returns:
            IndexInfo for the newly created index.
        """
        from arrow_lake.query.vector import VectorSearchBridge

        effective_metric = metric or self._config.vector.metric.value
        effective_index_type = index_type or self._config.vector.default_index_type.value

        self._get_storage().rebuild_vector_index(
            dataset_name,
            metric=effective_metric,
            vector_column=vector_column,
            index_type=effective_index_type,
            num_partitions=num_partitions,
            num_sub_vectors=num_sub_vectors,
        )

        bridge = self._get_component(
            "vector",
            lambda: VectorSearchBridge(
                self._get_storage(),
                config=self._config.vector,
                **self._bridge_kwargs(),
            ),
        )
        info = bridge.get_index_info(dataset_name, vector_column=vector_column)
        if info is None:
            from arrow_lake.exceptions import ErrorCode, QueryError

            raise QueryError(
                error_code=ErrorCode.VECTOR_INDEX_FAILED,
                message=f"Index rebuild completed but metadata unavailable for '{dataset_name}'",
            )
        return info

    def delete_fts_index(self, dataset_name: str) -> None:
        """Delete the full-text search index from a dataset.

        Args:
            dataset_name: Name of the Lance dataset.

        Raises:
            StorageError: If dataset not found or index deletion fails.
        """
        table = self._get_storage().open_dataset(dataset_name)
        try:
            indices = list(table.list_indices())
            for idx_config in indices:
                idx_name = idx_config.name if hasattr(idx_config, "name") else str(idx_config)
                cols = idx_config.columns if hasattr(idx_config, "columns") else []
                if cols and any("fts" in c.lower() or "tantivy" in c.lower() for c in cols):
                    self._get_storage().delete_vector_index(dataset_name, idx_name)
                    return
                if "fts" in idx_name.lower() or "tantivy" in idx_name.lower():
                    self._get_storage().delete_vector_index(dataset_name, idx_name)
                    return
        except (ValueError, RuntimeError, OSError):
            pass

    def get_fts_index_info(self, dataset_name: str) -> dict[str, Any] | None:
        """Get information about the full-text search index on a dataset.

        Args:
            dataset_name: Name of the Lance dataset.

        Returns:
            Dict with index metadata if an FTS index exists, None otherwise.
        """
        table = self._get_storage().open_dataset(dataset_name)
        try:
            indices = list(table.list_indices())
            for idx_config in indices:
                idx_name = idx_config.name if hasattr(idx_config, "name") else str(idx_config)
                if "fts" in idx_name.lower() or "tantivy" in idx_name.lower():
                    return {
                        "name": idx_name,
                        "columns": list(idx_config.columns) if hasattr(idx_config, "columns") else [],
                        "index_type": str(idx_config.index_type) if hasattr(idx_config, "index_type") else "fts",
                    }
        except (ValueError, RuntimeError, OSError):
            pass
        return None
