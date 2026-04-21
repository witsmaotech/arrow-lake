"""RAG pipeline benchmarks — M4.

Benchmarks RAG end-to-end latency: retrieval, context assembly, and LLM generation.
Uses MockLLMProvider to isolate pipeline overhead from LLM latency.
"""

from __future__ import annotations

import asyncio

import pytest
from arrow_lake.config import RAGConfig
from arrow_lake.rag.pipeline import RAGPipeline
from arrow_lake.rag.provider import LLMMessage, LLMResponse

from tests.benchmark.benchmark_report import BenchmarkReport


class _MockLLMProvider:
    """Mock LLM provider with configurable latency for benchmarking."""

    def __init__(self, latency_ms: float = 0) -> None:
        self._latency_s = latency_ms / 1000.0
        self._call_count = 0

    async def generate(self, messages: list[LLMMessage]) -> LLMResponse:
        self._call_count += 1
        if self._latency_s > 0:
            await asyncio.sleep(self._latency_s)
        return LLMResponse(
            content="This is a mock RAG response for benchmarking purposes.",
            model="mock-model",
            usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
            finish_reason="stop",
            provider="mock",
        )

    async def generate_stream(self, messages: list[LLMMessage]):
        response = await self.generate(messages)
        for word in response.content.split():
            yield word + " "

    async def close(self) -> None:
        pass


def _mock_retriever(question: str, dataset_name: str, top_k: int) -> list[dict]:
    """Mock retriever that returns fixed documents."""
    docs = [
        {
            "text": "Machine learning is a subset of artificial intelligence that enables systems to learn from data.",
            "score": 0.95,
            "metadata": {"source": "doc_001"},
        },
        {
            "text": "Deep learning uses neural networks with multiple layers to model complex patterns in data.",
            "score": 0.90,
            "metadata": {"source": "doc_002"},
        },
        {
            "text": "Natural language processing combines computational linguistics with statistical models.",
            "score": 0.85,
            "metadata": {"source": "doc_003"},
        },
    ]
    return docs[:top_k]


def _make_rag_pipeline(
    llm: _MockLLMProvider | None = None,
) -> RAGPipeline:
    """Create a RAGPipeline with mock components."""
    config = RAGConfig(
        top_k=3,
        max_context_tokens=500,
        system_prompt="You are a helpful assistant.",
    )
    return RAGPipeline(
        llm=llm or _MockLLMProvider(),
        retriever=_mock_retriever,
        config=config,
    )


@pytest.mark.benchmark
class TestRAGPipelineBenchmark:
    """Benchmark RAG pipeline end-to-end latency."""

    def test_rag_query_latency(self) -> None:
        """Benchmark: single RAG query latency (mock LLM, zero latency)."""
        pipeline = _make_rag_pipeline(_MockLLMProvider(latency_ms=0))

        report = BenchmarkReport("rag_query_latency")

        async def _run() -> None:
            await pipeline.query(
                question="What is machine learning?",
                dataset_name="bench_rag",
            )

        report.measure(
            "single RAG query (zero LLM latency)",
            lambda: asyncio.get_event_loop().run_until_complete(_run()),
            repeats=20,
            warmup=5,
        )
        report.print_summary()
        print(report.to_json())

    def test_rag_query_latency_with_simulated_llm(self) -> None:
        """Benchmark: RAG query with 50ms simulated LLM latency."""
        pipeline = _make_rag_pipeline(_MockLLMProvider(latency_ms=50))

        report = BenchmarkReport("rag_query_with_llm")

        async def _run() -> None:
            await pipeline.query(
                question="Explain deep learning architectures.",
                dataset_name="bench_rag",
            )

        report.measure(
            "single RAG query (50ms simulated LLM)",
            lambda: asyncio.get_event_loop().run_until_complete(_run()),
            repeats=10,
            warmup=3,
        )
        report.print_summary()
        print(report.to_json())

    def test_rag_query_throughput(self) -> None:
        """Benchmark: RAG queries per second (mock LLM, zero latency)."""
        pipeline = _make_rag_pipeline(_MockLLMProvider(latency_ms=0))

        report = BenchmarkReport("rag_query_throughput")
        n_queries = 50

        async def _batch() -> None:
            for i in range(n_queries):
                await pipeline.query(
                    question=f"Question number {i}?",
                    dataset_name="bench_rag",
                )

        report.measure(
            f"{n_queries} sequential queries",
            lambda: asyncio.get_event_loop().run_until_complete(_batch()),
            rows=n_queries,
            repeats=3,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())

    def test_rag_streaming_latency(self) -> None:
        """Benchmark: RAG streaming query time-to-first-token."""
        pipeline = _make_rag_pipeline(_MockLLMProvider(latency_ms=0))

        report = BenchmarkReport("rag_streaming_ttfb")

        async def _run() -> None:
            chunks: list[str] = []
            async for chunk in pipeline.query_stream(
                question="What is machine learning?",
                dataset_name="bench_rag",
            ):
                chunks.append(chunk)

        report.measure(
            "streaming query (time to complete)",
            lambda: asyncio.get_event_loop().run_until_complete(_run()),
            repeats=10,
            warmup=3,
        )
        report.print_summary()
        print(report.to_json())

    def test_rag_context_assembly(self) -> None:
        """Benchmark: context assembly overhead (no LLM call)."""
        from arrow_lake.rag.context import ContextCitation, ContextWindow

        _make_rag_pipeline()

        report = BenchmarkReport("rag_context_assembly")

        # Directly benchmark context window building
        def _build_context() -> None:
            citations = tuple(
                ContextCitation(
                    text="Machine learning enables systems to learn from data using statistical models.",
                    score=0.95,
                    source="doc_001",
                    start_char=0,
                    end_char=100,
                )
                for _ in range(5)
            )
            ContextWindow(
                question="What is ML?",
                context_chunks=citations,
                total_tokens=500,
                top_k=5,
            )

        report.measure(
            "build context window (5 citations)",
            _build_context,
            repeats=1000,
            warmup=10,
        )
        report.print_summary()
        print(report.to_json())
