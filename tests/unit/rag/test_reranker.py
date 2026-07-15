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
        # _parse_score parses the FIRST integer: "Rating: 9" → 9
        assert LLMReranker._parse_score("Rating: 9") == 9.0

    def test_no_digits_returns_default(self) -> None:
        assert LLMReranker._parse_score("none") == 5.0

    def test_zero_clamped_to_one(self) -> None:
        assert LLMReranker._parse_score("0") == 1.0

    def test_greater_than_ten_clamped(self) -> None:
        # First integer 42 → clamped to 10 (previously scanned last digit '2' → 2)
        assert LLMReranker._parse_score("42") == 10.0

    def test_negative_ignored_takes_first_digit(self) -> None:
        assert LLMReranker._parse_score("-3") == 3.0

    def test_score_ten_not_inverted(self) -> None:
        # Regression: old reversed-single-digit logic mapped "10" → 0 → 1.
        assert LLMReranker._parse_score("10") == 10.0
        assert LLMReranker._parse_score("Rating: 10") == 10.0


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
# _LakeRAGMixin._build_reranker wiring (Defect A: rag.reranker was dead config)
# ===========================================================================


class TestBuildRerankerWiring:
    def _mixin(self, reranker: str, model: str = "BAAI/bge-reranker-v2-m3"):
        from arrow_lake._lake_rag import _LakeRAGMixin

        m = object.__new__(_LakeRAGMixin)
        rag = MagicMock(reranker=reranker, reranker_model=model)
        m.config = MagicMock(rag=rag)
        return m

    def test_none_returns_noop(self) -> None:
        assert isinstance(self._mixin("none")._build_reranker(MagicMock()), NoopReranker)

    def test_llm_returns_llm_reranker(self) -> None:
        from arrow_lake.rag.reranker import LLMReranker as _LLMR

        assert isinstance(self._mixin("llm")._build_reranker(MagicMock()), _LLMR)

    def test_cross_encoder_returns_cross_encoder(self) -> None:
        from arrow_lake.rag.reranker import CrossEncoderReranker as _CER

        assert isinstance(self._mixin("cross-encoder")._build_reranker(MagicMock()), _CER)


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

    def test_ollama_kind(self) -> None:
        from arrow_lake.rag.reranker import OllamaReranker

        r = create_reranker("ollama", base_url="http://localhost:11434", model_name="m")
        assert isinstance(r, OllamaReranker)

    def test_ollama_kind_without_base_url_fallback(self) -> None:
        assert isinstance(create_reranker("ollama", base_url=""), NoopReranker)


# ===========================================================================
# OllamaReranker (Qwen3-Reranker yes/no judge)
# ===========================================================================


class TestOllamaReranker:
    @pytest.mark.asyncio
    async def test_no_base_url_passthrough(self) -> None:
        from arrow_lake.rag.reranker import OllamaReranker

        r = OllamaReranker("m", base_url="")
        chunks = [_chunk("a"), _chunk("b")]
        assert await r.rerank("q", chunks, top_n=2) == chunks

    @pytest.mark.asyncio
    async def test_empty_chunks(self) -> None:
        from arrow_lake.rag.reranker import OllamaReranker

        r = OllamaReranker("m", base_url="http://x")
        assert await r.rerank("q", [], top_n=5) == []

    @pytest.mark.asyncio
    async def test_yes_ranks_above_no(self) -> None:
        from arrow_lake.rag.reranker import OllamaReranker

        r = OllamaReranker("m", base_url="http://x")
        # "bad" chunk has higher retrieval score but reranker says "no" → sinks.
        chunks = [_chunk("good match", score=0.1), _chunk("bad mismatch", score=0.9)]
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        probe = MagicMock(status_code=200)
        client.get = AsyncMock(return_value=probe)

        def post(url, json=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = lambda: None
            doc = json["messages"][1]["content"]
            resp.json.return_value = {"message": {"content": "yes" if "good match" in doc else "no"}}
            return resp

        client.post = AsyncMock(side_effect=post)
        with patch("httpx.AsyncClient", return_value=client):
            out = await r.rerank("q", chunks, top_n=2)
        assert out[0].text == "good match"
        assert out[0].metadata["rerank_score"] == 1.0
        assert out[1].metadata["rerank_score"] == 0.0

    @pytest.mark.asyncio
    async def test_unavailable_passthrough(self) -> None:
        from arrow_lake.rag.reranker import OllamaReranker

        r = OllamaReranker("m", base_url="http://x")
        chunks = [_chunk("a"), _chunk("b")]
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(side_effect=RuntimeError("conn refused"))  # probe fails
        with patch("httpx.AsyncClient", return_value=client):
            out = await r.rerank("q", chunks, top_n=2)
        assert out == chunks  # latched unavailable → passthrough
