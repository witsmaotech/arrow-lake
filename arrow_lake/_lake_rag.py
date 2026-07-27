"""RAG capabilities mixin for the Lake facade."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from arrow_lake.rag.pipeline import RAGPipeline, RAGResponse
from arrow_lake.validation import validate_identifier

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


class _LakeRAGMixin:
    """Provides RAG (Retrieval-Augmented Generation) capabilities."""

    def _get_rag_pipeline(self) -> RAGPipeline:
        """Lazily create and cache a RAGPipeline instance.

        When ``hugegraph.enabled=True``, attempts to create a
        :class:`GraphRAGPipeline` that augments vector retrieval with
        knowledge graph context.  Falls back to the base
        :class:`RAGPipeline` if KG component creation fails.
        """
        from arrow_lake.rag.provider import create_llm_provider

        def _factory() -> RAGPipeline:
            # extract/reranker LLM: config.rag.extract_llm → global llm (lightweight default).
            provider = create_llm_provider(self.config.rag.extract_llm or self.config.llm)
            # [#RAG-LLM-split] generation uses a capable QA model (config.rag.qa_llm,
            # e.g. qwen-max@百炼); reranker + KG extraction stay on `provider`. Falls
            # back to `provider` when qa_llm is None.
            gen_provider = (
                create_llm_provider(self.config.rag.qa_llm)
                if self.config.rag.qa_llm is not None
                else provider
            )
            reranker = self._build_reranker(provider)

            if not self.config.hugegraph.enabled:
                return RAGPipeline(
                    llm_provider=gen_provider,
                    config=self.config.rag,
                    retriever=self._rag_retriever,
                    context_window_tokens=self.config.llm.context_window_tokens,
                    reranker=reranker,
                    session_store=self._build_session_store(),
                )

            # Attempt GraphRAG pipeline with KG augmentation
            try:
                return self._create_graph_rag_pipeline(provider, gen_provider, reranker=reranker)
            except (OSError, ValueError, RuntimeError, ConnectionError):
                logger.warning(
                    "Failed to create GraphRAGPipeline, falling back to RAGPipeline",
                    exc_info=True,
                )
                return RAGPipeline(
                    llm_provider=gen_provider,
                    config=self.config.rag,
                    retriever=self._rag_retriever,
                    context_window_tokens=self.config.llm.context_window_tokens,
                    reranker=reranker,
                    session_store=self._build_session_store(),
                )

        return self._get_component("rag_pipeline", _factory)

    def _create_graph_rag_pipeline(self, provider: Any, gen_provider: Any = None, reranker: Any = None) -> RAGPipeline:
        """Create a GraphRAGPipeline with KG components.

        Raises:
            Exception: If KG component creation fails (caller handles fallback).
        """
        from arrow_lake.knowledge_graph.client import HugeGraphClient
        from arrow_lake.knowledge_graph.extractor import EntityExtractor
        from arrow_lake.knowledge_graph.retriever import KGRetriever
        from arrow_lake.rag.graph_rag import GraphRAGPipeline

        kg_client = HugeGraphClient(self.config.hugegraph)
        kg_extractor = EntityExtractor(provider)
        kg_retriever = KGRetriever(kg_client, self.config.hugegraph)

        return GraphRAGPipeline(
            llm_provider=gen_provider or provider,  # generation: capable qa_llm (falls back to global)
            config=self.config.rag,
            retriever=self._rag_retriever,
            kg_client=kg_client,
            kg_retriever=kg_retriever,
            kg_extractor=kg_extractor,
            context_window_tokens=self.config.llm.context_window_tokens,
            traversal_depth=self.config.hugegraph.default_traversal_depth,
            reranker=reranker,
            session_store=self._build_session_store(),
        )

    def _build_session_store(self) -> Any:
        """v1.9.0: wrap the libSQL RagSessionStore (set on the facade at
        startup) in a SessionStore. Returns None when system_db is disabled
        → pipeline keeps the in-memory default."""
        store = getattr(self, "_rag_session_store", None)
        if store is None:
            return None
        from arrow_lake.rag.session import SessionStore

        return SessionStore(session_store=store)

    def _build_reranker(self, provider: Any) -> Any:
        """Build a reranker from RAG config ('none' → Noop, zero overhead).

        Wired here so ``config.rag.reranker`` actually takes effect on the RAG
        pipeline. Previously this config was dead — the pipeline was always
        built without a reranker and silently used NoopReranker. For the ollama
        kind, ``base_url`` defaults to the embedding api_base (same ollama
        instance) when ``reranker_base_url`` is unset.
        """
        from arrow_lake.rag.reranker import create_reranker

        rag_cfg = self.config.rag
        base_url = getattr(rag_cfg, "reranker_base_url", "")
        if not base_url:
            emb_base = getattr(self.config.embedding, "api_base", "") or ""
            base_url = emb_base[:-3] if emb_base.endswith("/v1") else emb_base
        return create_reranker(
            rag_cfg.reranker,
            model_name=rag_cfg.reranker_model,
            provider=provider,
            base_url=base_url,
        )

    def _rag_retriever(
        self, question: str, dataset_name: str, top_k: int, strategy: str = "fts"
    ) -> Any:
        """Retrieve relevant documents for a RAG query.

        Dispatches by ``strategy``:
        - ``fts`` (or unknown): full-text search (BM25 + jieba).
        - ``vector``: embed question → vector similarity search.
        - ``hybrid``: embed question → vector + FTS RRF fusion.

        Returns a PyArrow table. Score column: fts→ ``_score``, hybrid→
        ``_rrf_score``; vector relies on the reranker (distance column is
        lower-is-better, so we let the reranker re-score rather than risk an
        inverted sort in ContextWindow.finalize).

        v1.9.5 批1: previously this ALWAYS ran fts regardless of
        ``default_retrieval_strategy`` (which was a dead config — the pipeline's
        strategy arg only picked the score-column name). Now the strategy
        forwarded by the pipeline is honored. See docs/v1.9.5-rag-quality-plan.md.
        """
        validate_identifier(dataset_name)

        # fts / unknown → existing FTS path (zero overhead, no embedding cost).
        if strategy not in ("vector", "hybrid"):
            result = self.text_search(dataset_name, question, top_k=top_k)
            return result.table

        # vector / hybrid need a query embedding.
        query_vector = self._embed_query(question)

        if strategy == "vector":
            result = self.search(dataset_name, query_vector, top_k=top_k)
            return result.table

        # hybrid: vector + FTS RRF fusion (default_retrieval_strategy).
        result = self.hybrid_search(dataset_name, query_vector, question, top_k=top_k)
        return result.table

    def _embed_query(self, question: str) -> list[float]:
        """Embed a query string via the configured embedding backend.

        Uses a cached raw encoder (NOT wrapped in _LakeEmbedderAdapter — RAG
        query embedding needs plain ``encode()``, not langchain Embeddings).
        """
        encoder = self._get_component("rag_query_embedder", self._create_query_embedder)
        batch = encoder.encode([question])
        vec = batch.embeddings[0]
        # EmbeddingBatch.embeddings is a numpy ndarray → list[float] for the
        # search/hybrid_search APIs.
        return vec.tolist() if hasattr(vec, "tolist") else list(vec)

    def _create_query_embedder(self) -> Any:
        """Build the query embedding encoder (singleton via _get_component).

        Mirrors :meth:`_LakeKG._create_kg_embedder`'s construction over
        ``ArrowLakeConfig.embedding`` but returns the RAW encoder. DAFT
        degrades to LOCAL for single-query embedding (Daft's distributed
        overhead is unjustified per-query).
        """
        from arrow_lake.config._enums import EmbeddingBackend
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder, LocalEmbeddingEncoder

        cfg = self.config.embedding
        if cfg.backend == EmbeddingBackend.OPENAI and cfg.api_base:
            return ApiEmbeddingEncoder(
                api_base=cfg.api_base, api_key=cfg.api_key,
                model_name=cfg.model, batch_size=cfg.batch_size,
            )
        # LOCAL / DAFT / RAY_SERVE → LOCAL (DAFT too heavy for per-query embed).
        return LocalEmbeddingEncoder(
            model_name=cfg.model, batch_size=cfg.batch_size,
            expected_dim=cfg.expected_dim,
        )

    async def rag_query(
        self,
        question: str,
        dataset_name: str,
        *,
        top_k: int | None = None,
        strategy: str | None = None,
        template_name: str | None = None,
        session_id: str | None = None,
        use_kg: bool = True,
    ) -> RAGResponse:
        """Run a RAG query over a dataset.

        Args:
            question: The user's question.
            dataset_name: Name of the Lance dataset to search.
            top_k: Number of documents to retrieve (default from config).
            strategy: Retrieval strategy ("fts", "vector", "hybrid").
            template_name: Prompt template name (default: "default_qa").
            session_id: Optional session ID for conversation history.

        Returns:
            RAGResponse with answer, citations, and metadata.
        """
        validate_identifier(dataset_name)
        pipeline = self._get_rag_pipeline()
        from arrow_lake.core.metrics import (
            get_metrics_enabled,
            query_latency_seconds,
            query_total,
        )

        if get_metrics_enabled():
            query_total.labels(query_type="rag_query").inc()
        t0 = time.monotonic()
        result = await pipeline.query(
            question=question,
            dataset_name=dataset_name,
            top_k=top_k,
            strategy=strategy,
            template_name=template_name,
            session_id=session_id,
            use_kg=use_kg,
        )
        if get_metrics_enabled():
            query_latency_seconds.labels(query_type="rag_query").observe(time.monotonic() - t0)
        from arrow_lake.catalog.lineage_hooks import auto_record_rag
        auto_record_rag(self._get_storage(), dataset_name, question)
        return result

    async def rag_query_stream(
        self,
        question: str,
        dataset_name: str,
        *,
        top_k: int | None = None,
        strategy: str | None = None,
        template_name: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream a RAG query response.

        Yields content deltas as they are generated by the LLM.
        """
        validate_identifier(dataset_name)
        pipeline = self._get_rag_pipeline()
        from arrow_lake.core.metrics import get_metrics_enabled, query_total

        if get_metrics_enabled():
            query_total.labels(query_type="rag_query_stream").inc()
        async for chunk in pipeline.query_stream(
            question=question,
            dataset_name=dataset_name,
            top_k=top_k,
            strategy=strategy,
            template_name=template_name,
        ):
            yield chunk

    async def rag_extract(
        self,
        dataset_name: str,
        *,
        text_column: str = "text_content",
        top_k: int | None = None,
        template_name: str | None = None,
    ) -> RAGResponse:
        """Extract entities from a dataset using RAG.

        Args:
            dataset_name: Name of the Lance dataset.
            text_column: Column containing text to extract from.
            top_k: Number of documents to process.
            template_name: Prompt template name (default: "entity_extract").

        Returns:
            RAGResponse with extracted entities in the answer field.
        """
        validate_identifier(dataset_name)
        pipeline = self._get_rag_pipeline()
        return await pipeline.extract_entities(
            dataset_name=dataset_name,
            text_column=text_column,
            top_k=top_k,
            template_name=template_name,
        )

    def rag_get_history(self, session_id: str) -> list[dict]:
        """Get conversation history for a session.

        Args:
            session_id: Session identifier.

        Returns:
            List of turn dicts sorted by turn_id, or empty list if no session store.
        """
        pipeline = self._get_rag_pipeline()
        if pipeline._session_store is None:
            return []
        return pipeline._session_store.get_history(session_id)

    async def rag_batch_query(
        self,
        questions: list[str],
        dataset_name: str,
        *,
        top_k: int | None = None,
        strategy: str | None = None,
        concurrency: int = 5,
    ) -> list[RAGResponse]:
        """Batch RAG query — concurrent fan-out with semaphore-limited parallelism.

        Args:
            questions: List of user questions.
            dataset_name: Target Lance dataset.
            top_k: Documents to retrieve per question.
            strategy: Retrieval strategy.
            concurrency: Max parallel queries.

        Returns:
            List of RAGResponse in the same order as questions.
        """
        validate_identifier(dataset_name)
        pipeline = self._get_rag_pipeline()
        return await pipeline.batch_query(
            questions=questions,
            dataset_name=dataset_name,
            top_k=top_k,
            strategy=strategy,
            concurrency=concurrency,
        )

    def rag_feedback(
        self,
        session_id: str,
        turn_id: int,
        rating: str,
        *,
        flagged_citations: list[int] | None = None,
        comment: str = "",
    ) -> None:
        """Submit feedback on a RAG response.

        Args:
            session_id: Session identifier.
            turn_id: Turn number in the session.
            rating: "positive", "negative", or "neutral".
            flagged_citations: Indices of citations to flag as unhelpful.
            comment: Optional freeform comment.
        """
        pipeline = self._get_rag_pipeline()
        if pipeline._session_store is None:
            return
        pipeline._session_store.save_feedback(
            session_id,
            turn_id,
            rating,
            flagged_citation_indices=tuple(flagged_citations) if flagged_citations else (),
            comment=comment,
        )

    def rag_get_feedback(self, session_id: str) -> list[dict]:
        """Get all feedback for a session."""
        pipeline = self._get_rag_pipeline()
        if pipeline._session_store is None:
            return []
        return pipeline._session_store.get_feedback(session_id)

    def rag_cleanup_expired_sessions(self) -> int:
        """Sweep and remove expired session turns. Returns count evicted."""
        pipeline = self._get_rag_pipeline()
        if pipeline._session_store is None:
            return 0
        return pipeline._session_store.cleanup_expired()
