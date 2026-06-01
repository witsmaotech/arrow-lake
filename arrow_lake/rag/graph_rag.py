"""Graph-augmented RAG pipeline (GraphRAG).

Extends RAGPipeline with knowledge graph context injection. The pipeline
extracts entities from the user question, retrieves relevant graph
triplets, and merges them into the context window alongside vector
retrieval results before LLM generation.

Graceful degradation: if KG components are unavailable or raise errors,
falls back to the parent RAGPipeline.query() (pure vector RAG).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from arrow_lake.rag.pipeline import RAGPipeline, RAGResponse

if TYPE_CHECKING:
    from arrow_lake.config import RAGConfig
    from arrow_lake.knowledge_graph.client import HugeGraphClient
    from arrow_lake.knowledge_graph.extractor import EntityExtractor
    from arrow_lake.knowledge_graph.retriever import KGRetriever
    from arrow_lake.rag.provider import BaseLLMProvider

logger = logging.getLogger(__name__)


class QuestionEntityCache:
    """TTL-based cache for question → extracted entities mapping."""

    def __init__(self, ttl: int = 300, max_size: int = 1000) -> None:
        self._cache: dict[str, tuple[float, list[str]]] = {}
        self._ttl = ttl
        self._max_size = max_size

    def get(self, question: str) -> list[str] | None:
        key = question
        entry = self._cache.get(key)
        if entry and time.time() - entry[0] < self._ttl:
            return entry[1]
        return None

    def set(self, question: str, entities: list[str]) -> None:
        key = question
        if len(self._cache) >= self._max_size:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest]
        self._cache[key] = (time.time(), entities)


class GraphRAGPipeline(RAGPipeline):
    """Graph-augmented RAG pipeline.

    Extends RAGPipeline with knowledge graph context injection. If KG
    components are unavailable, degrades gracefully to pure vector RAG
    via ``super().query()``.

    Args:
        llm_provider: LLM provider for generation.
        config: RAG pipeline configuration.
        retriever: Vector retrieval callback (question, dataset, top_k) -> Table.
        kg_client: HugeGraph client (may be ``None`` for degraded mode).
        kg_retriever: Knowledge graph retriever.
        kg_extractor: Entity extractor (LLM-based).
        graph_weight: RRF fusion weight for graph vs vector context (0.0-1.0).
        traversal_depth: Max hops for graph traversal.
        max_graph_triplets: Maximum triplets to include in context.
    """

    def __init__(
        self,
        llm_provider: BaseLLMProvider,
        config: RAGConfig,
        retriever,
        kg_client: HugeGraphClient | None,
        kg_retriever: KGRetriever,
        kg_extractor: EntityExtractor,
        *,
        graph_weight: float = 0.3,
        traversal_depth: int = 2,
        max_graph_triplets: int = 50,
        **kwargs: object,
    ) -> None:
        super().__init__(llm_provider, config, retriever, **kwargs)
        self._kg_client = kg_client
        self._kg_retriever = kg_retriever
        self._kg_extractor = kg_extractor
        self._graph_weight = max(0.0, min(1.0, graph_weight))
        self._traversal_depth = traversal_depth
        self._max_graph_triplets = max_graph_triplets
        self._entity_cache = QuestionEntityCache()

    # ------------------------------------------------------------------
    # KG availability
    # ------------------------------------------------------------------

    def _kg_available(self) -> bool:
        """Check whether KG components are available for use."""
        return self._kg_client is not None

    # ------------------------------------------------------------------
    # Entity extraction from question (with cache)
    # ------------------------------------------------------------------

    async def _extract_question_entities(self, question: str) -> list[str]:
        """Extract entity names from a question via the entity extractor.

        Results are cached with a 300s TTL to avoid re-extracting for
        similar/duplicate questions.

        Returns:
            List of entity name strings found in the question.
        """
        cached = self._entity_cache.get(question)
        if cached is not None:
            return cached

        try:
            result = await self._kg_extractor.extract(question, chunk_id="question")
            entities = [e.name for e in result.entities]
            self._entity_cache.set(question, entities)
            return entities
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Entity extraction from question failed, continuing without entities",
                exc_info=True,
            )
            return []

    # ------------------------------------------------------------------
    # Graph context retrieval
    # ------------------------------------------------------------------

    async def _retrieve_graph_context(
        self, question: str, entities: list[str]
    ) -> str:
        """Retrieve graph triplets and serialize to text.

        Returns:
            Serialized triplets text, or empty string on any failure.
        """
        if not entities:
            return ""
        try:
            graph_result = await self._kg_retriever.retrieve(
                question,
                extracted_entities=entities,
                traversal_depth=self._traversal_depth,
                max_triplets=self._max_graph_triplets,
            )
            return self._kg_retriever.triplets_to_text(graph_result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Graph retrieval failed, degrading to vector-only context",
                exc_info=True,
            )
            return ""

    # ------------------------------------------------------------------
    # RRF fusion for graph + vector context
    # ------------------------------------------------------------------

    def _fuse_context_with_weight(
        self, vector_text: str, graph_text: str
    ) -> str:
        """Fuse vector and graph context using graph_weight for emphasis.

        graph_weight controls how much graph context is emphasized relative
        to vector context. At 0.5, both are equal. At 0.3 (default),
        vector context gets more weight.
        """
        if not graph_text:
            return vector_text
        if not vector_text:
            return f"== Knowledge Graph Context ==\n{graph_text}"

        # Use graph_weight to decide section ordering and emphasis
        graph_section = f"== Knowledge Graph Context (weight={self._graph_weight:.1f}) ==\n{graph_text}"
        vec_section = f"== Document Context (weight={1 - self._graph_weight:.1f}) ==\n{vector_text}"

        if self._graph_weight > 0.5:
            return f"{graph_section}\n\n{vec_section}"
        return f"{vec_section}\n\n{graph_section}"

    # ------------------------------------------------------------------
    # Main query (graph-augmented)
    # ------------------------------------------------------------------

    async def query(
        self,
        question: str,
        dataset_name: str,
        *,
        top_k: int | None = None,
        strategy: str | None = None,
        template_name: str | None = None,
        session_id: str | None = None,
    ) -> RAGResponse:
        """GraphRAG query with graceful degradation.

        When KG is available:
        1. Extract entities from the question (cached).
        2. Retrieve graph triplets in parallel with vector retrieval.
        3. RRF-fuse graph + vector context using graph_weight.
        4. Generate answer via LLM.

        When KG is unavailable or raises an error, falls back to
        ``super().query()`` (pure vector RAG).
        """
        if not self._kg_available():
            logger.debug("KG unavailable, falling back to vector RAG")
            return await super().query(
                question,
                dataset_name,
                top_k=top_k,
                strategy=strategy,
                template_name=template_name,
                session_id=session_id,
            )

        t0 = time.monotonic()
        effective_top_k = top_k or self._config.default_top_k
        effective_template = template_name or "graph_qa"

        try:
            # Step 1: Extract entities from the question (cached)
            entities = await self._extract_question_entities(question)

            # Step 2: Parallel vector + graph retrieval
            vector_task = asyncio.ensure_future(
                super()._retrieve_and_build_context(
                    question, dataset_name, effective_top_k, strategy,
                )
            )
            graph_task = asyncio.ensure_future(
                self._retrieve_graph_context(question, entities)
            )

            try:
                window, context_text = await vector_task
                graph_text = await graph_task
            except Exception:
                graph_task.cancel()
                raise

            # Step 3: RRF-fuse graph + vector context
            if graph_text:
                fused_text = self._fuse_context_with_weight(context_text, graph_text)
            else:
                fused_text = context_text

            # Step 4: Build messages and call LLM
            # Load session history for multi-turn
            history = None
            if session_id and self._session_store and self._config.history_injection_enabled:
                history = self._session_store.get_history(session_id)

            messages = self._build_messages(
                question, fused_text, effective_template, history=history,
            )
            llm_response = await self._llm.generate(messages)

            # Build response
            elapsed = (time.monotonic() - t0) * 1000
            citations = self._extract_citations(window)
            return RAGResponse(
                answer=llm_response.content,
                citations=citations,
                retrieval_count=window.chunk_count,
                context_tokens=window.token_count if window.token_count > 0 else None,
                llm_usage=llm_response.usage,
                latency_ms=round(elapsed, 1),
                session_id=session_id,
            )

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "GraphRAG query failed, falling back to vector RAG",
                exc_info=True,
            )
            return await super().query(
                question,
                dataset_name,
                top_k=top_k,
                strategy=strategy,
                template_name=template_name,
                session_id=session_id,
            )
