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
from collections.abc import Awaitable
from typing import TYPE_CHECKING

from arrow_lake.rag.pipeline import RAGPipeline

if TYPE_CHECKING:
    from arrow_lake.config import RAGConfig
    from arrow_lake.knowledge_graph.client import HugeGraphClient
    from arrow_lake.knowledge_graph.extractor import EntityExtractor
    from arrow_lake.knowledge_graph.retriever import KGRetriever
    from arrow_lake.rag.provider import BaseLLMProvider

logger = logging.getLogger(__name__)


class QuestionEntityCache:
    """TTL-based cache for question → extracted entities mapping.

    Uses ``time.monotonic`` so a wall-clock jump (NTP step, manual time
    change) can't mass-evict or mass-retain entries — ``time.time`` made the
    TTL brittle under clock skew. Monotonic time never goes backwards."""

    def __init__(self, ttl: int = 300, max_size: int = 1000) -> None:
        self._cache: dict[str, tuple[float, list[str]]] = {}
        self._ttl = ttl
        self._max_size = max_size

    def get(self, question: str) -> list[str] | None:
        key = question
        entry = self._cache.get(key)
        if entry and time.monotonic() - entry[0] < self._ttl:
            return entry[1]
        return None

    def set(self, question: str, entities: list[str]) -> None:
        key = question
        if len(self._cache) >= self._max_size:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest]
        self._cache[key] = (time.monotonic(), entities)


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
    # Template-method hooks — base RAGPipeline.query() is the single template;
    # GraphRAG overrides only these hooks so parity (messages, verification,
    # latency_breakdown, save_turn) can never drift again.
    # ------------------------------------------------------------------

    def _extra_context_task(self, question: str, dataset_name: str, use_kg: bool) -> Awaitable[str] | None:
        """GraphRAG: kick off entity extraction + graph triplet retrieval to
        run in PARALLEL with vector retrieval (the base template gathers them).

        Returning None when KG is off/unavailable makes the base template run
        pure vector RAG — that IS the graceful degradation, with no duplicated
        super().query() fallback. Entity extraction + graph retrieval already
        swallow their own errors (→ [] / ""), so graph-side failure degrades
        to vector-only without propagating to the template.
        """
        if not use_kg or not self._kg_available():
            return None
        return self._retrieve_graph_context_for(question)

    async def _retrieve_graph_context_for(self, question: str) -> str:
        """Extract question entities (cached) → retrieve graph triplets."""
        entities = await self._extract_question_entities(question)
        return await self._retrieve_graph_context(question, entities)

    def _fuse_extra_context(self, context_text: str, extra_text: str) -> str:
        """GraphRAG: weighted graph/document fusion (section ordering by graph_weight)."""
        return self._fuse_context_with_weight(context_text, extra_text)
