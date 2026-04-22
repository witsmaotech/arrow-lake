"""Tests for RAG Pipeline orchestration — M2 Day 6."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from arrow_lake.config import RAGConfig
from arrow_lake.rag.pipeline import RAGCitation, RAGPipeline, RAGResponse
from arrow_lake.rag.prompt import PromptRegistry, PromptTemplate, PromptType
from arrow_lake.rag.provider import LLMResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_llm_response(text: str, model: str = "test-model") -> LLMResponse:
    return LLMResponse(
        content=text,
        model=model,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        finish_reason="stop",
        provider="openai",
    )


def _mock_provider(response_text: str) -> MagicMock:
    provider = MagicMock()
    provider.generate = AsyncMock(return_value=_mock_llm_response(response_text))
    provider.generate_stream = AsyncMock()
    provider.close = AsyncMock()
    return provider


def _mock_retrieve(result_table) -> MagicMock:
    """Create a mock retriever function that returns a PyArrow table."""
    return MagicMock(return_value=result_table)


def _make_result_table(texts: list[str], row_ids: list[str], scores: list[float] | None = None):
    """Create a simple PyArrow table matching search result format."""
    import pyarrow as pa

    if scores is None:
        scores = [float(i + 1) / len(texts) for i in range(len(texts))]

    return pa.table({
        "text": texts,
        "row_id": row_ids,
        "_score": scores,
    })


# ---------------------------------------------------------------------------
# RAGCitation
# ---------------------------------------------------------------------------


class TestRAGCitation:
    def test_construction(self) -> None:
        cite = RAGCitation(
            chunk_index=0,
            dataset="docs",
            row_id="r1",
            score=0.95,
            text_excerpt="Some excerpt",
        )
        assert cite.chunk_index == 0
        assert cite.dataset == "docs"
        assert cite.row_id == "r1"

    def test_frozen(self) -> None:
        cite = RAGCitation(
            chunk_index=0, dataset="d", row_id="r", score=1.0, text_excerpt="t"
        )
        with pytest.raises(AttributeError):
            cite.score = 0.5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RAGResponse
# ---------------------------------------------------------------------------


class TestRAGResponse:
    def test_construction(self) -> None:
        resp = RAGResponse(
            answer="Hello!",
            citations=(RAGCitation(0, "d", "r", 0.9, "ex"),),
            retrieval_count=3,
            context_tokens=50,
            llm_usage={"total_tokens": 15},
            latency_ms=120.5,
        )
        assert resp.answer == "Hello!"
        assert len(resp.citations) == 1
        assert resp.retrieval_count == 3
        assert resp.latency_ms == 120.5

    def test_optional_fields(self) -> None:
        resp = RAGResponse(answer="OK", citations=(), retrieval_count=0)
        assert resp.context_tokens is None
        assert resp.llm_usage is None
        assert resp.latency_ms is None
        assert resp.session_id is None


# ---------------------------------------------------------------------------
# RAGPipeline
# ---------------------------------------------------------------------------


class TestRAGPipeline:
    @pytest.fixture()
    def rag_config(self) -> RAGConfig:
        return RAGConfig(enabled=True, default_top_k=5, max_context_chunks=10)

    @pytest.mark.asyncio
    async def test_query_success(self, rag_config: RAGConfig) -> None:
        table = _make_result_table(
            ["First document text", "Second document text"],
            ["doc-1", "doc-2"],
            [0.95, 0.85],
        )
        provider = _mock_provider("Based on the documents, the answer is 42.")

        pipeline = RAGPipeline(
            llm_provider=provider,
            config=rag_config,
            retriever=lambda q, ds, top_k: table,
        )

        resp = await pipeline.query(
            question="What is the answer?",
            dataset_name="documents",
        )

        assert resp.answer == "Based on the documents, the answer is 42."
        assert resp.retrieval_count == 2
        assert resp.latency_ms is not None
        assert resp.latency_ms > 0

    @pytest.mark.asyncio
    async def test_query_with_custom_template(self, rag_config: RAGConfig) -> None:
        table = _make_result_table(["Data"], ["r1"], [1.0])
        provider = _mock_provider("Summary here")

        registry = PromptRegistry()
        registry.register(PromptTemplate(
            name="custom",
            type=PromptType.SUMMARY,
            template="Summarize: {{ context }}\n\nText: {{ question }}\n\nSummary:",
        ))

        pipeline = RAGPipeline(
            llm_provider=provider,
            config=rag_config,
            retriever=lambda q, ds, top_k: table,
            prompt_registry=registry,
        )

        resp = await pipeline.query(
            question="Summarize this",
            dataset_name="docs",
            template_name="custom",
        )

        assert resp.answer == "Summary here"
        # Verify the LLM was called with messages containing the custom template
        call_args = provider.generate.call_args
        messages = call_args[0][0]
        assert any("Summarize:" in m.content for m in messages)

    @pytest.mark.asyncio
    async def test_query_with_top_k(self, rag_config: RAGConfig) -> None:
        table = _make_result_table(
            ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"],
            [f"r{i}" for i in range(10)],
        )
        provider = _mock_provider("Answer")

        # Use a MagicMock retriever to verify call args
        mock_retriever = MagicMock(return_value=table)

        pipeline = RAGPipeline(
            llm_provider=provider,
            config=rag_config,
            retriever=mock_retriever,
        )

        await pipeline.query(
            question="Q",
            dataset_name="docs",
            top_k=3,
        )

        # Retriever should be called with top_k=3
        retriever_call = mock_retriever.call_args
        assert retriever_call[0][2] == 3

    @pytest.mark.asyncio
    async def test_query_with_system_prompt(self, rag_config: RAGConfig) -> None:
        config = RAGConfig(
            enabled=True,
            system_prompt="You are a helpful assistant.",
        )
        table = _make_result_table(["Context"], ["r1"], [1.0])
        provider = _mock_provider("Response")

        pipeline = RAGPipeline(
            llm_provider=provider,
            config=config,
            retriever=lambda q, ds, top_k: table,
        )

        await pipeline.query(question="Q", dataset_name="docs")

        call_args = provider.generate.call_args
        messages = call_args[0][0]
        assert any("helpful assistant" in m.content for m in messages)

    @pytest.mark.asyncio
    async def test_query_stream(self, rag_config: RAGConfig) -> None:
        table = _make_result_table(["Data"], ["r1"], [1.0])

        async def mock_stream(messages):
            for chunk in ["Hello", " world"]:
                yield chunk

        provider = MagicMock()
        provider.generate_stream = mock_stream
        provider.close = AsyncMock()

        pipeline = RAGPipeline(
            llm_provider=provider,
            config=rag_config,
            retriever=lambda q, ds, top_k: table,
        )

        chunks = []
        async for chunk in pipeline.query_stream(
            question="Q", dataset_name="docs"
        ):
            chunks.append(chunk)

        assert chunks == ["Hello", " world"]

    @pytest.mark.asyncio
    async def test_extract_entities(self, rag_config: RAGConfig) -> None:
        table = _make_result_table(
            ["John Smith works at Acme Corp in New York."],
            ["r1"],
            [1.0],
        )
        provider = _mock_provider('{"entities": ["John Smith", "Acme Corp", "New York"]}')

        pipeline = RAGPipeline(
            llm_provider=provider,
            config=rag_config,
            retriever=lambda q, ds, top_k: table,
        )

        resp = await pipeline.extract_entities(
            dataset_name="docs",
            text_column="text",
        )

        assert resp.answer == '{"entities": ["John Smith", "Acme Corp", "New York"]}'

    @pytest.mark.asyncio
    async def test_empty_retrieval_returns_no_context_warning(self, rag_config: RAGConfig) -> None:
        import pyarrow as pa

        empty_table = pa.table({
            "text": pa.array([], type=pa.string()),
            "row_id": pa.array([], type=pa.string()),
        })
        provider = _mock_provider("I don't have enough context.")

        pipeline = RAGPipeline(
            llm_provider=provider,
            config=rag_config,
            retriever=lambda q, ds, top_k: empty_table,
        )

        result = await pipeline.query(question="Q", dataset_name="docs")
        assert result.retrieval_count == 0
        assert result.answer is not None

    @pytest.mark.asyncio
    async def test_citations_in_response(self, rag_config: RAGConfig) -> None:
        table = _make_result_table(
            ["Alpha", "Beta"],
            ["r1", "r2"],
            [0.9, 0.8],
        )
        config = RAGConfig(enabled=True, enable_citations=True)
        provider = _mock_provider("Answer with citations [1][2]")

        pipeline = RAGPipeline(
            llm_provider=provider,
            config=config,
            retriever=lambda q, ds, top_k: table,
        )

        resp = await pipeline.query(question="Q", dataset_name="docs")
        assert len(resp.citations) == 2
        assert resp.citations[0].dataset == "docs"
        assert resp.citations[0].row_id == "r1"

    @pytest.mark.asyncio
    async def test_citations_disabled(self, rag_config: RAGConfig) -> None:
        table = _make_result_table(["Text"], ["r1"], [1.0])
        config = RAGConfig(enabled=True, enable_citations=False)
        provider = _mock_provider("Answer")

        pipeline = RAGPipeline(
            llm_provider=provider,
            config=config,
            retriever=lambda q, ds, top_k: table,
        )

        resp = await pipeline.query(question="Q", dataset_name="docs")
        assert resp.citations == ()
