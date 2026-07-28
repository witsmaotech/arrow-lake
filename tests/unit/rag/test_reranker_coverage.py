"""Tests for RAG reranker module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from arrow_lake.rag.context import ContextChunk
from arrow_lake.rag.reranker import (
    CrossEncoderReranker,
    LLMReranker,
    NoopReranker,
    create_reranker,
)


def _chunk(text: str, score: float = 0.5) -> ContextChunk:
    return ContextChunk(text=text, dataset="ds1", row_id=0, score=score)


# ── NoopReranker ──


class TestNoopReranker:
    @pytest.mark.asyncio
    async def test_truncates_to_top_n(self) -> None:
        r = NoopReranker()
        chunks = [_chunk(f"text{i}") for i in range(5)]
        result = await r.rerank("query", chunks, 3)
        assert len(result) == 3

    def test_name_property(self) -> None:
        assert NoopReranker().name == "NoopReranker"


# ── CrossEncoderReranker ──


class TestCrossEncoderReranker:
    @pytest.mark.asyncio
    async def test_empty_chunks(self) -> None:
        r = CrossEncoderReranker()
        assert await r.rerank("q", [], 5) == []

    @pytest.mark.asyncio
    async def test_fallback_to_noop(self) -> None:
        r = CrossEncoderReranker()
        with pytest.MonkeyPatch.context() as m:
            m.setattr(r, "_load_model", lambda: None)
            result = await r.rerank("q", [_chunk("a"), _chunk("b")], 5)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_rerank_with_model(self) -> None:
        r = CrossEncoderReranker()
        mock_model = MagicMock()
        mock_model.predict.return_value = MagicMock(tolist=lambda: [0.9, 0.1])
        r._model = mock_model

        result = await r.rerank("q", [_chunk("a"), _chunk("b")], 2)
        assert len(result) == 2
        assert result[0].metadata["rerank_score"] == 0.9

    @pytest.mark.asyncio
    async def test_predict_failure_falls_back(self) -> None:
        r = CrossEncoderReranker()
        mock_model = MagicMock()
        mock_model.predict.side_effect = RuntimeError("OOM")
        r._model = mock_model

        result = await r.rerank("q", [_chunk("a")], 1)
        assert len(result) == 1


# ── LLMReranker ──


class TestLLMReranker:
    @pytest.mark.asyncio
    async def test_empty_chunks(self) -> None:
        r = LLMReranker(provider=MagicMock())
        assert await r.rerank("q", [], 5) == []

    @pytest.mark.asyncio
    async def test_rerank_with_provider(self) -> None:
        provider = AsyncMock()
        provider.generate.return_value = MagicMock(content="8")
        r = LLMReranker(provider=provider)

        result = await r.rerank("q", [_chunk("a", 0.5), _chunk("b", 0.3)], 2)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_provider_failure_uses_original_score(self) -> None:
        provider = AsyncMock()
        provider.generate.side_effect = RuntimeError("timeout")
        r = LLMReranker(provider=provider)

        result = await r.rerank("q", [_chunk("a", 0.7)], 1)
        assert len(result) == 1
        assert result[0].score == 0.7


# ── _parse_score ──


class TestParseScore:
    def test_extracts_digit(self) -> None:
        assert LLMReranker._parse_score("8") == 8.0

    def test_extracts_from_text(self) -> None:
        assert LLMReranker._parse_score("Rating: 9") == 9.0

    def test_defaults_to_five(self) -> None:
        assert LLMReranker._parse_score("no digits here") == 5.0

    def test_clamps_max(self) -> None:
        # First integer 15 → clamped to 10 (previously '5' from reversed '15')
        assert LLMReranker._parse_score("15") == 10.0

    def test_clamps_min(self) -> None:
        assert LLMReranker._parse_score("0") == 1.0


# ── create_reranker ──


class TestCreateReranker:
    def test_none_kind(self) -> None:
        assert isinstance(create_reranker("none"), NoopReranker)

    def test_empty_kind(self) -> None:
        assert isinstance(create_reranker(""), NoopReranker)

    def test_cross_encoder(self) -> None:
        assert isinstance(create_reranker("cross-encoder"), CrossEncoderReranker)

    def test_llm_with_provider(self) -> None:
        assert isinstance(create_reranker("llm", provider=MagicMock()), LLMReranker)

    def test_llm_without_provider_fallback(self) -> None:
        assert isinstance(create_reranker("llm", provider=None), NoopReranker)

    def test_unknown_kind_fallback(self) -> None:
        assert isinstance(create_reranker("bogus"), NoopReranker)
