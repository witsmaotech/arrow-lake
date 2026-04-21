"""Spike tests for LLM latency and connectivity — M2 Day 5 NO-GO gate.

These tests verify local LLM provider connectivity and latency.
They require a running vLLM or Ollama instance.

Run with:
    uv run pytest tests/spike/test_llm_latency.py -v -m benchmark
"""

from __future__ import annotations

import time

import pytest
from arrow_lake.config import LLMConfig, LLMProviderType
from arrow_lake.rag.context import count_tokens
from arrow_lake.rag.provider import LLMMessage, create_llm_provider

pytestmark = pytest.mark.benchmark


def _is_service_up(host: str, port: int) -> bool:
    """Check if a service is reachable."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except (OSError, ConnectionRefusedError):
        return False


# ---------------------------------------------------------------------------
# vLLM tests
# ---------------------------------------------------------------------------


class TestVLLMLatency:
    @pytest.fixture(autouse=True)
    def _skip_if_no_vllm(self) -> None:
        if not _is_service_up("localhost", 8000):
            pytest.skip("vLLM not running on localhost:8000")

    @pytest.mark.asyncio
    async def test_vllm_max_latency_under_5s(self) -> None:
        """Max latency of 20 vLLM generate() calls should be under 5 seconds."""
        config = LLMConfig(
            provider=LLMProviderType.VLLM,
            model="Qwen/Qwen3-0.6B",
            max_tokens=64,
        )
        provider = create_llm_provider(config)
        try:
            times: list[float] = []
            for _ in range(20):
                start = time.perf_counter()
                resp = await provider.generate([LLMMessage(role="user", content="Say hello.")])
                elapsed = time.perf_counter() - start
                times.append(elapsed)

            p95 = sorted(times)[int(len(times) * 0.95)]
            assert p95 < 5.0, f"vLLM P95 latency {p95:.2f}s exceeds 5s"
            assert len(resp.content) > 0
        finally:
            await provider.close()

    @pytest.mark.asyncio
    async def test_vllm_sse_streaming_works(self) -> None:
        """vLLM SSE streaming should return content chunks."""
        config = LLMConfig(
            provider=LLMProviderType.VLLM,
            model="Qwen/Qwen3-0.6B",
            max_tokens=64,
        )
        provider = create_llm_provider(config)
        try:
            chunks: list[str] = []
            async for chunk in provider.generate_stream(
                [LLMMessage(role="user", content="Count to 5.")]
            ):
                chunks.append(chunk)

            assert len(chunks) > 0, "Stream produced no chunks"
            full_text = "".join(chunks)
            assert len(full_text) > 0
        finally:
            await provider.close()


# ---------------------------------------------------------------------------
# Ollama tests
# ---------------------------------------------------------------------------


class TestOllamaLatency:
    @pytest.fixture(autouse=True)
    def _skip_if_no_ollama(self) -> None:
        if not _is_service_up("localhost", 11434):
            pytest.skip("Ollama not running on localhost:11434")

    @pytest.mark.asyncio
    async def test_ollama_max_latency_under_5s(self) -> None:
        """Max latency of 20 Ollama generate() calls should be under 5 seconds."""
        config = LLMConfig(
            provider=LLMProviderType.OLLAMA,
            model="qwen3:0.6b",
            max_tokens=64,
        )
        provider = create_llm_provider(config)
        try:
            times: list[float] = []
            for _ in range(20):
                start = time.perf_counter()
                resp = await provider.generate([LLMMessage(role="user", content="Say hello.")])
                elapsed = time.perf_counter() - start
                times.append(elapsed)

            p95 = sorted(times)[int(len(times) * 0.95)]
            assert p95 < 5.0, f"Ollama P95 latency {p95:.2f}s exceeds 5s"
            assert len(resp.content) > 0
        finally:
            await provider.close()

    @pytest.mark.asyncio
    async def test_ollama_sse_streaming_works(self) -> None:
        """Ollama SSE streaming should return content chunks."""
        config = LLMConfig(
            provider=LLMProviderType.OLLAMA,
            model="qwen3:0.6b",
            max_tokens=64,
        )
        provider = create_llm_provider(config)
        try:
            chunks: list[str] = []
            async for chunk in provider.generate_stream(
                [LLMMessage(role="user", content="Count to 5.")]
            ):
                chunks.append(chunk)

            assert len(chunks) > 0, "Stream produced no chunks"
        finally:
            await provider.close()


# ---------------------------------------------------------------------------
# Context assembly benchmarks
# ---------------------------------------------------------------------------


class TestContextAssemblyLatency:
    def test_context_assembly_latency_under_500ms(self) -> None:
        """Assembling 20 chunks should be under 500ms."""
        from arrow_lake.rag.context import ContextChunk, ContextWindow

        window = ContextWindow(token_budget=4096)
        chunks = [
            ContextChunk(
                text=f"This is chunk number {i} with some content.",
                dataset="bench",
                row_id=str(i),
                score=float(i) / 20,
            )
            for i in range(20)
        ]

        start = time.perf_counter()
        for c in chunks:
            window.add_chunk(c)
        assembled = window.assemble()
        elapsed = time.perf_counter() - start

        assert elapsed < 0.5, f"Context assembly took {elapsed:.3f}s"
        assert len(assembled) > 0

    def test_token_counting_latency_under_500ms(self) -> None:
        """Counting tokens for a 10K char text should be under 500ms."""
        text = "Hello world " * 500  # ~6000 chars

        start = time.perf_counter()
        count = count_tokens(text)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.5, f"Token counting took {elapsed:.3f}s"
        assert count > 0
