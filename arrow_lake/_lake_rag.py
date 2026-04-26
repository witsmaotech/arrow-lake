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
            provider = create_llm_provider(self._config.llm)

            if not self._config.hugegraph.enabled:
                return RAGPipeline(
                    llm_provider=provider,
                    config=self._config.rag,
                    retriever=self._rag_retriever,
                    context_window_tokens=self._config.llm.context_window_tokens,
                )

            # Attempt GraphRAG pipeline with KG augmentation
            try:
                return self._create_graph_rag_pipeline(provider)
            except (OSError, ValueError, RuntimeError, ConnectionError):
                logger.warning(
                    "Failed to create GraphRAGPipeline, falling back to RAGPipeline",
                    exc_info=True,
                )
                return RAGPipeline(
                    llm_provider=provider,
                    config=self._config.rag,
                    retriever=self._rag_retriever,
                    context_window_tokens=self._config.llm.context_window_tokens,
                )

        return self._get_component("rag_pipeline", _factory)

    def _create_graph_rag_pipeline(self, provider: Any) -> RAGPipeline:
        """Create a GraphRAGPipeline with KG components.

        Raises:
            Exception: If KG component creation fails (caller handles fallback).
        """
        from arrow_lake.knowledge_graph.client import HugeGraphClient
        from arrow_lake.knowledge_graph.extractor import EntityExtractor
        from arrow_lake.knowledge_graph.retriever import KGRetriever
        from arrow_lake.rag.graph_rag import GraphRAGPipeline

        kg_client = HugeGraphClient(self._config.hugegraph)
        kg_extractor = EntityExtractor(provider)
        kg_retriever = KGRetriever(kg_client, self._config.hugegraph)

        return GraphRAGPipeline(
            llm_provider=provider,
            config=self._config.rag,
            retriever=self._rag_retriever,
            kg_client=kg_client,
            kg_retriever=kg_retriever,
            kg_extractor=kg_extractor,
            context_window_tokens=self._config.llm.context_window_tokens,
            traversal_depth=self._config.hugegraph.default_traversal_depth,
        )

    def _rag_retriever(self, question: str, dataset_name: str, top_k: int) -> Any:
        """Retrieve relevant documents for a RAG query.

        Uses FTS (full-text search) by default. Override for custom retrieval.
        Returns a PyArrow table with columns: text, row_id, _score.
        """
        validate_identifier(dataset_name)
        result = self.text_search(
            dataset_name,
            question,
            top_k=top_k,
        )
        return result.table

    async def rag_query(
        self,
        question: str,
        dataset_name: str,
        *,
        top_k: int | None = None,
        strategy: str | None = None,
        template_name: str | None = None,
        session_id: str | None = None,
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
        )
        if get_metrics_enabled():
            query_latency_seconds.labels(query_type="rag_query").observe(time.monotonic() - t0)
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
        text_column: str = "text",
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
