"""Tests for GraphRAG pipeline -- M3 Week 3 Day 1-2."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pyarrow as pa
import pytest
from arrow_lake.config import RAGConfig
from arrow_lake.knowledge_graph.extractor import (
    ExtractedEntity,
    ExtractionResult,
)
from arrow_lake.knowledge_graph.retriever import (
    GraphRetrievalResult,
    GraphTriplet,
)
from arrow_lake.rag.graph_rag import GraphRAGPipeline
from arrow_lake.rag.provider import LLMResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_llm_response(text: str = "Answer") -> LLMResponse:
    return LLMResponse(
        content=text,
        model="test-model",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        finish_reason="stop",
        provider="openai",
    )


def _mock_llm_provider(response_text: str = "Answer") -> MagicMock:
    provider = MagicMock()
    provider.generate = AsyncMock(return_value=_mock_llm_response(response_text))
    provider.close = AsyncMock()
    return provider


def _mock_retriever():
    """Return a mock retriever that yields an empty table."""
    table = pa.table({
        "text_content": ["Document content"],
        "row_id": ["r1"],
        "_score": [0.9],
    })
    return MagicMock(return_value=table)


def _mock_kg_extractor(entities: list[str] | None = None) -> MagicMock:
    """Mock EntityExtractor that returns the given entity names."""
    extractor = MagicMock()
    if entities is None:
        entities = ["EntityA", "EntityB"]
    result = ExtractionResult(
        entities=tuple(ExtractedEntity(name=e, entity_type="concept") for e in entities),
        relations=(),
        raw_text="",
    )
    extractor.extract = AsyncMock(return_value=result)
    return extractor


def _mock_kg_retriever(triplets_text: str = "A --related_to_B--> B") -> MagicMock:
    """Mock KGRetriever that returns graph triplets."""
    retriever = MagicMock()
    graph_result = GraphRetrievalResult(
        query_entities=("A",),
        triplets=(GraphTriplet(subject="A", predicate="related_to_B", object_="B"),),
        traversal_depth=2,
        vertex_count=1,
        edge_count=1,
    )
    retriever.retrieve = AsyncMock(return_value=graph_result)
    retriever.triplets_to_text = MagicMock(return_value=triplets_text)
    return retriever


def _mock_kg_client() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def rag_config() -> RAGConfig:
    return RAGConfig(enabled=True, default_top_k=5, max_context_chunks=10)


def _make_pipeline(
    rag_config: RAGConfig,
    kg_client=None,
    kg_retriever=None,
    kg_extractor=None,
    llm_response: str = "Answer",
):
    provider = _mock_llm_provider(llm_response)
    return GraphRAGPipeline(
        llm_provider=provider,
        config=rag_config,
        retriever=_mock_retriever(),
        kg_client=kg_client,
        kg_retriever=kg_retriever or _mock_kg_retriever(),
        kg_extractor=kg_extractor or _mock_kg_extractor(),
    )


# ---------------------------------------------------------------------------
# _kg_available
# ---------------------------------------------------------------------------


class TestKgAvailable:
    def test_kg_available_when_client_exists(self, rag_config: RAGConfig) -> None:
        pipeline = _make_pipeline(rag_config, kg_client=_mock_kg_client())
        assert pipeline._kg_available() is True

    def test_kg_unavailable_when_client_is_none(self, rag_config: RAGConfig) -> None:
        pipeline = _make_pipeline(rag_config, kg_client=None)
        assert pipeline._kg_available() is False


# ---------------------------------------------------------------------------
# Fallback to vector RAG
# ---------------------------------------------------------------------------


class TestFallback:
    @pytest.mark.asyncio
    async def test_query_fallback_to_vector_rag(
        self, rag_config: RAGConfig
    ) -> None:
        pipeline = _make_pipeline(rag_config, kg_client=None)

        resp = await pipeline.query(
            question="What is the answer?",
            dataset_name="documents",
        )

        assert resp.answer == "Answer"
        # KG components should NOT have been called
        pipeline._kg_extractor.extract.assert_not_called()  # type: ignore[attr-defined]
        pipeline._kg_retriever.retrieve.assert_not_called()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Graph context injection
# ---------------------------------------------------------------------------


class TestGraphContext:
    @pytest.mark.asyncio
    async def test_query_with_graph_context(self, rag_config: RAGConfig) -> None:
        pipeline = _make_pipeline(
            rag_config,
            kg_client=_mock_kg_client(),
            kg_retriever=_mock_kg_retriever("A --rel--> B"),
            kg_extractor=_mock_kg_extractor(["A"]),
            llm_response="Graph-enhanced answer",
        )

        resp = await pipeline.query(
            question="What is A?",
            dataset_name="documents",
        )

        assert resp.answer == "Graph-enhanced answer"
        # Entity extraction should have been called
        pipeline._kg_extractor.extract.assert_called_once()  # type: ignore[attr-defined]
        # Graph retrieval should have been called
        pipeline._kg_retriever.retrieve.assert_called_once()  # type: ignore[attr-defined]
        # triplets_to_text should have been called
        pipeline._kg_retriever.triplets_to_text.assert_called_once()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Entity extraction from question
# ---------------------------------------------------------------------------


class TestExtractQuestionEntities:
    @pytest.mark.asyncio
    async def test_extract_question_entities(self, rag_config: RAGConfig) -> None:
        pipeline = _make_pipeline(
            rag_config,
            kg_extractor=_mock_kg_extractor(["Python", "Django"]),
        )

        entities = await pipeline._extract_question_entities(
            "What is the best web framework for Python?"
        )

        assert entities == ["Python", "Django"]

    @pytest.mark.asyncio
    async def test_extract_question_entities_failure(
        self, rag_config: RAGConfig
    ) -> None:
        extractor = MagicMock()
        extractor.extract = AsyncMock(side_effect=RuntimeError("LLM failed"))
        pipeline = _make_pipeline(rag_config, kg_extractor=extractor)

        entities = await pipeline._extract_question_entities("A question")

        assert entities == []


# ---------------------------------------------------------------------------
# Graph context retrieval
# ---------------------------------------------------------------------------


class TestRetrieveGraphContext:
    @pytest.mark.asyncio
    async def test_retrieve_graph_context(self, rag_config: RAGConfig) -> None:
        pipeline = _make_pipeline(
            rag_config,
            kg_retriever=_mock_kg_retriever("X --knows--> Y"),
        )

        text = await pipeline._retrieve_graph_context("Question?", ["X"])

        assert text == "X --knows--> Y"
        pipeline._kg_retriever.retrieve.assert_called_once()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_retrieve_graph_context_empty_entities(
        self, rag_config: RAGConfig
    ) -> None:
        pipeline = _make_pipeline(rag_config)

        text = await pipeline._retrieve_graph_context("Question?", [])

        assert text == ""
        pipeline._kg_retriever.retrieve.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_retrieve_graph_context_error(
        self, rag_config: RAGConfig
    ) -> None:
        retriever = MagicMock()
        retriever.retrieve = AsyncMock(side_effect=RuntimeError("KG error"))
        pipeline = _make_pipeline(rag_config, kg_retriever=retriever)

        text = await pipeline._retrieve_graph_context("Question?", ["X"])

        assert text == ""

    @pytest.mark.asyncio
    async def test_query_graceful_degradation_on_exception(
        self, rag_config: RAGConfig
    ) -> None:
        """If GraphRAG query fails mid-way, fall back to vector RAG."""
        extractor = MagicMock()
        extractor.extract = AsyncMock(side_effect=RuntimeError("extraction crash"))
        pipeline = _make_pipeline(
            rag_config,
            kg_client=_mock_kg_client(),
            kg_extractor=extractor,
        )

        # Should NOT raise -- falls back to super().query()
        resp = await pipeline.query(
            question="What is X?",
            dataset_name="documents",
        )

        assert resp.answer == "Answer"
