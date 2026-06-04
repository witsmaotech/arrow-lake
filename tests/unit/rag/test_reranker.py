"""Tests for rag/reranker.py — NoopReranker, LLMReranker parsing, and factory function."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arrow_lake.rag.context import ContextChunk
from arrow_lake.rag.reranker import (
    CrossEncoderReranker,
    LLMReranker,
    NoopReranker,
    create_reranker,
)


def _chunk(text: str = "hello", score: float = 0.5) -> ContextChunk:
    return ContextChunk(text=text, dataset="ds", row_id="r1", score=score)


# ===========================================================================
# NoopReranker
# ===========================================================================


class TestNoopReranker:
    def test_truncates_to_top_n(self) -> None:
        chunks = [_chunk(f"t{i}") for i in range(5)]
        result = NoopReranker().rerank("q", chunks, top_n=3)
        assert len(result) == 3
        assert result[0].text == "t0"

    def test_top_n_exceeds_length(self) -> None:
        chunks = [_chunk()]
        result = NoopReranker().rerank("q", chunks, top_n=10)
        assert len(result) == 1

    def test_empty_chunks(self) -> None:
        result = NoopReranker().rerank("q", [], top_n=5)
        assert result == []

    def test_name_property(self) -> None:
        assert NoopReranker().name == "NoopReranker"


# ===========================================================================
# CrossEncoderReranker
# ===========================================================================


class TestCrossEncoderReranker:
    def test_fallback_when_model_load_fails(self) -> None:
        """Model loading fails → falls back to NoopReranker behavior."""
        reranker = CrossEncoderReranker()
        reranker._model = None  # ensure no cached model
        chunks = [_chunk(f"t{i}") for i in range(3)]
        with patch.object(reranker, "_load_model", return_value=None):
            result = reranker.rerank("q", chunks, top_n=2)
        assert len(result) == 2
        assert result[0].text == "t0"

    def test_empty_chunks_returns_empty(self) -> None:
        reranker = CrossEncoderReranker()
        assert reranker.rerank("q", [], top_n=5) == []


# ===========================================================================
# LLMReranker — _parse_score
# ===========================================================================


class TestLLMParseScore:
    def test_single_digit(self) -> None:
        assert LLMReranker._parse_score("7") == 7.0

    def test_digit_in_sentence(self) -> None:
        # _parse_score reads reversed, so last digit in "Rating: 9" is '9'
        assert LLMReranker._parse_score("Rating: 9") == 9.0

    def test_no_digits_returns_default(self) -> None:
        assert LLMReranker._parse_score("none") == 5.0

    def test_zero_clamped_to_one(self) -> None:
        assert LLMReranker._parse_score("0") == 1.0

    def test_greater_than_ten_clamped(self) -> None:
        assert LLMReranker._parse_score("42") == 2.0  # last digit '2'

    def test_negative_ignored_takes_last_digit(self) -> None:
        assert LLMReranker._parse_score("-3") == 3.0


# ===========================================================================
# LLMReranker — async rerank
# ===========================================================================


class TestLLMRerankerAsync:
    @pytest.mark.asyncio
    async def test_rerank_with_mock_provider(self) -> None:
        provider = MagicMock()
        resp = MagicMock()
        resp.content = "8"
        provider.generate = AsyncMock(return_value=resp)
        reranker = LLMReranker(provider=provider, max_chunks=5)
        chunks = [_chunk(f"t{i}", score=float(i)) for i in range(3)]
        result = await reranker.rerank("q", chunks, top_n=2)
        assert len(result) == 2
        # All get same score 8.0, so original order preserved
        assert result[0].metadata["rerank_score"] == 8.0

    @pytest.mark.asyncio
    async def test_rerank_empty(self) -> None:
        reranker = LLMReranker(provider=MagicMock())
        result = await reranker.rerank("q", [], top_n=5)
        assert result == []

    @pytest.mark.asyncio
    async def test_rerank_provider_failure_fallback(self) -> None:
        provider = MagicMock()
        provider.generate = AsyncMock(side_effect=RuntimeError("LLM down"))
        reranker = LLMReranker(provider=provider)
        chunks = [_chunk("a", score=0.9), _chunk("b", score=0.1)]
        result = await reranker.rerank("q", chunks, top_n=2)
        # Falls back to original scores
        assert len(result) == 2


# ===========================================================================
# create_reranker factory
# ===========================================================================


class TestCreateReranker:
    def test_none_kind(self) -> None:
        assert isinstance(create_reranker("none"), NoopReranker)

    def test_empty_kind(self) -> None:
        assert isinstance(create_reranker(""), NoopReranker)

    def test_cross_encoder_kind(self) -> None:
        r = create_reranker("cross-encoder")
        assert isinstance(r, CrossEncoderReranker)

    def test_llm_kind_with_provider(self) -> None:
        r = create_reranker("llm", provider=MagicMock())
        assert isinstance(r, LLMReranker)

    def test_llm_kind_without_provider_fallback(self) -> None:
        r = create_reranker("llm", provider=None)
        assert isinstance(r, NoopReranker)

    def test_unknown_kind_fallback(self) -> None:
        assert isinstance(create_reranker("unknown"), NoopReranker)
