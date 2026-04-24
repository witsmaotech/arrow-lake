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
        graph_weight: Reserved for future RRF fusion weight.
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
        self._graph_weight = graph_weight
        self._traversal_depth = traversal_depth
        self._max_graph_triplets = max_graph_triplets

    # ------------------------------------------------------------------
    # KG availability
    # ------------------------------------------------------------------

    def _kg_available(self) -> bool:
        """Check whether KG components are available for use."""
        return self._kg_client is not None

    # ------------------------------------------------------------------
    # Entity extraction from question
    # ------------------------------------------------------------------

    async def _extract_question_entities(self, question: str) -> list[str]:
        """Extract entity names from a question via the entity extractor.

        Returns:
            List of entity name strings found in the question.
        """
        try:
            result = await self._kg_extractor.extract(question, chunk_id="question")
            return [e.name for e in result.entities]
        except asyncio.CancelledError:
            raise
        except (AttributeError, TypeError, ValueError, OSError, RuntimeError):
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
        except (AttributeError, TypeError, ValueError, OSError, RuntimeError):
            logger.warning(
                "Graph retrieval failed, degrading to vector-only context",
                exc_info=True,
            )
            return ""

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
        1. Extract entities from the question.
        2. Retrieve graph triplets in parallel with vector retrieval.
        3. Merge graph context into the context window.
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
            # Step 1: Extract entities from the question
            entities = await self._extract_question_entities(question)

            # Step 2: Parallel vector + graph retrieval
            vector_task = super()._retrieve_and_build_context(
                question, dataset_name, effective_top_k, strategy,
            )
            graph_task = asyncio.ensure_future(
                self._retrieve_graph_context(question, entities)
            )

            window, context_text = await vector_task
            graph_text = await graph_task

            # Step 3: Add graph context to the window
            if graph_text:
                window.add_graph_context(graph_text)
                # Re-assemble with graph context included
                context_text = window.assemble()

            # Step 4: Build messages and call LLM
            messages = self._build_messages(question, context_text, effective_template)
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
        except (AttributeError, TypeError, ValueError, OSError, RuntimeError):
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
