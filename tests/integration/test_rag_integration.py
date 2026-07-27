"""Integration tests for RAG pipeline — M2 Day 10.

Uses MockLLMProvider to test end-to-end flow without real LLM.
"""

from __future__ import annotations

import pytest
from arrow_lake.config import RAGConfig
from arrow_lake.rag.pipeline import RAGPipeline
from arrow_lake.rag.provider import LLMMessage, LLMResponse
from arrow_lake.rag.session import SessionStore

# ---------------------------------------------------------------------------
# Mock LLM Provider
# ---------------------------------------------------------------------------


class MockLLMProvider:
    """A mock LLM provider for integration tests."""

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses = responses or {}
        self._call_count = 0

    async def generate(self, messages: list[LLMMessage]) -> LLMResponse:
        self._call_count += 1
        last_msg = messages[-1].content if messages else ""

        # Match response by keyword or return default
        answer = "I don't have a specific answer for that."
        for keyword, resp in self._responses.items():
            if keyword.lower() in last_msg.lower():
                answer = resp
                break

        return LLMResponse(
            content=answer,
            model="mock-model",
            usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            finish_reason="stop",
            provider="mock",
        )

    async def generate_stream(self, messages: list[LLMMessage]):
        response = await self.generate(messages)
        # Yield word by word
        for word in response.content.split():
            yield word + " "

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Mock retriever
# ---------------------------------------------------------------------------


def _mock_retriever(question: str, dataset_name: str, top_k: int, strategy: str = "fts"):
    """Return a PyArrow table based on question keywords."""
    import pyarrow as pa

    docs = {
        "python": [
            ("Python is a programming language.", "py-1", 0.95),
            ("Python has dynamic typing.", "py-2", 0.85),
            ("Python is great for data science.", "py-3", 0.75),
        ],
        "arrow": [
            ("Apache Arrow is a columnar format.", "arrow-1", 0.9),
            ("Arrow supports zero-copy reads.", "arrow-2", 0.8),
        ],
    }

    # Find matching documents
    matched = []
    for keyword, entries in docs.items():
        if keyword.lower() in question.lower():
            matched.extend(entries)

    if not matched:
        matched = docs.get("python", docs.get("arrow", []))

    texts, row_ids, scores = zip(*matched[:top_k], strict=True)
    return pa.table({
        "text_content": list(texts),
        "row_id": list(row_ids),
        "_score": list(scores),
    })


# ---------------------------------------------------------------------------
# E2E Integration Tests
# ---------------------------------------------------------------------------


class TestRAGIntegration:
    @pytest.fixture()
    def pipeline(self) -> RAGPipeline:
        provider = MockLLMProvider(responses={
            "python": "Python is a versatile programming language used for web development, data science, and automation.",
            "arrow": "Apache Arrow provides efficient columnar memory format for analytics.",
        })
        return RAGPipeline(
            llm_provider=provider,
            config=RAGConfig(
                enabled=True,
                default_top_k=3,
                enable_citations=True,
            ),
            retriever=_mock_retriever,
        )

    @pytest.mark.asyncio
    async def test_e2e_rag_query_python(self, pipeline: RAGPipeline) -> None:
        resp = await pipeline.query(
            question="Tell me about Python",
            dataset_name="knowledge_base",
        )
        assert resp.answer is not None
        assert len(resp.answer) > 0
        assert "python" in resp.answer.lower() or "Python" in resp.answer
        assert resp.retrieval_count > 0
        assert resp.latency_ms is not None
        assert resp.llm_usage is not None

    @pytest.mark.asyncio
    async def test_e2e_rag_query_with_citations(self, pipeline: RAGPipeline) -> None:
        resp = await pipeline.query(
            question="Tell me about Python",
            dataset_name="knowledge_base",
        )
        assert len(resp.citations) > 0
        assert resp.citations[0].dataset == "knowledge_base"
        assert resp.citations[0].score > 0

    @pytest.mark.asyncio
    async def test_e2e_streaming(self, pipeline: RAGPipeline) -> None:
        chunks = []
        async for chunk in pipeline.query_stream(
            question="Tell me about Python",
            dataset_name="knowledge_base",
        ):
            chunks.append(chunk)

        full_text = "".join(chunks).strip()
        assert len(full_text) > 0

    @pytest.mark.asyncio
    async def test_e2e_entity_extraction(self, pipeline: RAGPipeline) -> None:
        resp = await pipeline.extract_entities(
            dataset_name="knowledge_base",
        )
        assert resp.answer is not None
        assert resp.retrieval_count > 0


class TestRAGSessionIntegration:
    @pytest.mark.asyncio
    async def test_e2e_session_persistence(self) -> None:
        session_store = SessionStore()
        provider = MockLLMProvider(responses={
            "capital": "Paris is the capital of France.",
            "population": "France has a population of about 67 million.",
        })

        pipeline = RAGPipeline(
            llm_provider=provider,
            config=RAGConfig(enabled=True, default_top_k=2),
            retriever=_mock_retriever,
            session_store=session_store,
        )

        # Turn 1
        resp1 = await pipeline.query(
            question="What is the capital of France?",
            dataset_name="geo",
            session_id="sess-1",
        )
        assert "Paris" in resp1.answer

        # Turn 2
        await pipeline.query(
            question="What is the population of France?",
            dataset_name="geo",
            session_id="sess-1",
        )

        # Verify session history
        history = session_store.get_history("sess-1")
        assert len(history) == 2
        assert history[0]["question"] == "What is the capital of France?"
        assert history[1]["question"] == "What is the population of France?"
