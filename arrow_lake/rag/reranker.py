"""Reranking stage for RAG pipeline.

Provides pluggable rerankers that re-score and reorder retrieved chunks
before context assembly, improving answer relevance.

Three implementations:
- NoopReranker: passthrough (default, zero overhead)
- CrossEncoderReranker: cross-encoder model (e.g., bge-reranker-v2-m3)
- LLMReranker: LLM-based scoring (1-10 scale)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from arrow_lake.rag.context import ContextChunk

logger = logging.getLogger(__name__)


class BaseReranker(ABC):
    """Abstract base class for rerankers."""

    @abstractmethod
    def rerank(
        self,
        query: str,
        chunks: list[ContextChunk],
        top_n: int,
    ) -> list[ContextChunk]:
        """Re-score and reorder chunks, returning the top-N."""

    @property
    def name(self) -> str:
        return type(self).__name__


class NoopReranker(BaseReranker):
    """Passthrough reranker — no re-scoring, just truncates to top_n."""

    def rerank(
        self,
        query: str,
        chunks: list[ContextChunk],
        top_n: int,
    ) -> list[ContextChunk]:
        return chunks[:top_n]


class CrossEncoderReranker(BaseReranker):
    """Cross-encoder reranker using sentence-transformers models.

    Loads a cross-encoder model on first use. Falls back to NoopReranker
    if the model cannot be loaded (missing dependency, OOM, etc.).

    Args:
        model_name: HuggingFace model identifier for cross-encoder.
        max_length: Max token length for cross-encoder input.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        max_length: int = 512,
    ) -> None:
        self._model_name = model_name
        self._max_length = max_length
        self._model: Any = None
        self._fallback = NoopReranker()

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name, max_length=self._max_length)
            return self._model
        except Exception:
            logger.warning(
                "Failed to load cross-encoder '%s', falling back to noop reranker",
                self._model_name,
                exc_info=True,
            )
            return None

    def rerank(
        self,
        query: str,
        chunks: list[ContextChunk],
        top_n: int,
    ) -> list[ContextChunk]:
        if not chunks:
            return []

        model = self._load_model()
        if model is None:
            return self._fallback.rerank(query, chunks, top_n)

        pairs = [(query, c.text) for c in chunks]
        try:
            scores = model.predict(pairs)
        except Exception:
            logger.warning("Cross-encoder predict failed, returning original order", exc_info=True)
            return chunks[:top_n]

        # Sort by score descending
        scored = list(zip(chunks, scores.tolist(), strict=False))
        scored.sort(key=lambda x: x[1], reverse=True)

        # Update chunk scores to reflect reranking
        results: list[ContextChunk] = []
        for chunk, score in scored[:top_n]:
            results.append(ContextChunk(
                text=chunk.text,
                dataset=chunk.dataset,
                row_id=chunk.row_id,
                score=score,
                metadata={**(chunk.metadata or {}), "rerank_score": score, "original_score": chunk.score},
            ))
        return results


class LLMReranker(BaseReranker):
    """LLM-based reranker — asks the LLM to score each chunk 1-10.

    Uses the same LLM provider as the generation step. Slower but
    effective when cross-encoder models are unavailable.

    Args:
        provider: LLM provider to use for scoring.
        max_chunks: Maximum chunks to score (avoids excessive LLM calls).
    """

    _SCORE_PROMPT = (
        "Rate how relevant this document chunk is to the query on a scale of 1-10.\n"
        "Output ONLY a single integer number, nothing else.\n\n"
        "Query: {query}\n\nDocument: {text}\n\nRating:"
    )

    def __init__(self, provider: Any, max_chunks: int = 20) -> None:
        self._provider = provider
        self._max_chunks = max_chunks

    async def rerank(
        self,
        query: str,
        chunks: list[ContextChunk],
        top_n: int,
    ) -> list[ContextChunk]:
        if not chunks:
            return []

        from arrow_lake.rag.provider import LLMMessage

        to_score = chunks[:self._max_chunks]
        results: list[tuple[ContextChunk, float]] = []

        for chunk in to_score:
            prompt = self._SCORE_PROMPT.format(query=query, text=chunk.text[:1000])
            try:
                resp = await self._provider.generate([
                    LLMMessage(role="user", content=prompt),
                ])
                score = self._parse_score(resp.content)
            except Exception:
                score = chunk.score
            results.append((chunk, score))

        results.sort(key=lambda x: x[1], reverse=True)

        return [
            ContextChunk(
                text=c.text,
                dataset=c.dataset,
                row_id=c.row_id,
                score=s,
                metadata={**(c.metadata or {}), "rerank_score": s, "original_score": c.score},
            )
            for c, s in results[:top_n]
        ]

    @staticmethod
    def _parse_score(text: str) -> float:
        """Extract a 1-10 score from LLM output."""
        text = text.strip()
        for char in reversed(text):
            if char.isdigit():
                return float(min(max(int(char), 1), 10))
        return 5.0


def create_reranker(
    kind: str,
    *,
    model_name: str = "BAAI/bge-reranker-v2-m3",
    provider: Any = None,
) -> BaseReranker:
    """Factory: create a reranker by kind string."""
    if kind == "none" or not kind:
        return NoopReranker()
    if kind == "cross-encoder":
        return CrossEncoderReranker(model_name=model_name)
    if kind == "llm":
        if provider is None:
            logger.warning("LLM reranker requires a provider, falling back to noop")
            return NoopReranker()
        return LLMReranker(provider=provider)
    logger.warning("Unknown reranker kind '%s', falling back to noop", kind)
    return NoopReranker()
