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

        import asyncio

        from arrow_lake.rag.provider import LLMMessage

        to_score = chunks[:self._max_chunks]

        async def _score_one(chunk: ContextChunk) -> tuple[ContextChunk, float]:
            prompt = self._SCORE_PROMPT.format(query=query, text=chunk.text[:1000])
            try:
                resp = await self._provider.generate([
                    LLMMessage(role="user", content=prompt),
                ])
                return chunk, self._parse_score(resp.content)
            except Exception:
                return chunk, chunk.score

        # Score concurrently (was: sequential await per chunk — N round-trips).
        results = await asyncio.gather(*(_score_one(c) for c in to_score))
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
        """Extract a 1-10 score from LLM output.

        Parses the FIRST integer found (``"Rating: 8"`` → 8, ``"10"`` → 10) and
        clamps to 1-10. Previously this scanned for the last single digit,
        which mapped ``"10"`` → 0 → 1 (inverting the top score).
        """
        import re

        m = re.search(r"\d+", text or "")
        if not m:
            return 5.0
        return float(min(max(int(m.group()), 1), 10))


class OllamaReranker(BaseReranker):
    """Ollama-hosted reranker using the Qwen3-Reranker yes/no judge prompt.

    Scores each chunk by asking the model (e.g. ``dengcao/Qwen3-Reranker-0.6B``)
    whether the document is relevant to the query — binary ``yes``/``no`` mapped
    to 1.0/0.0 (ollama has no logprob API, so scoring is binary). Scoring is
    concurrent. Gracefully degrades to passthrough (Noop) when ollama or the
    model is unavailable, so RAG queries never break.

    Args:
        model_name: ollama model tag (e.g. ``dengcao/Qwen3-Reranker-0.6B:F16``).
        base_url: ollama root URL (no ``/v1``), e.g. ``http://localhost:11434``.
        max_chunks: cap on chunks scored per query.
        timeout: per-request timeout (seconds).
        api_key: optional bearer token (ollama usually needs none).
    """

    _JUDGE_SYSTEM = (
        'Judge whether the Document meets the requirements based on the Query '
        'and the Instruct provided. Note that the answer can only be "yes" or "no".'
    )

    def __init__(
        self,
        model_name: str,
        base_url: str,
        *,
        max_chunks: int = 20,
        timeout: float = 30.0,
        api_key: str | None = None,
    ) -> None:
        self._model = model_name
        self._base_url = (base_url or "").rstrip("/")
        self._max_chunks = max_chunks
        self._timeout = timeout
        self._api_key = api_key
        self._checked = False
        self._unavailable = False

    async def rerank(
        self,
        query: str,
        chunks: list[ContextChunk],
        top_n: int,
    ) -> list[ContextChunk]:
        import asyncio

        import httpx

        if not chunks:
            return []
        if not self._base_url:
            logger.warning("OllamaReranker has no base_url — passthrough")
            return chunks[:top_n]

        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            # One cheap upness probe (latched) so a missing ollama doesn't cost
            # N×timeout per query.
            if not self._checked:
                self._checked = True
                try:
                    pr = await client.get(f"{self._base_url}/api/tags")
                    self._unavailable = pr.status_code >= 400
                except Exception:
                    self._unavailable = True
                if self._unavailable:
                    logger.warning(
                        "Ollama reranker unavailable at %s — passthrough (Noop)",
                        self._base_url,
                    )
                    return chunks[:top_n]

            to_score = chunks[: self._max_chunks]

            async def _judge(chunk: ContextChunk) -> tuple[ContextChunk, float]:
                payload = {
                    "model": self._model,
                    "stream": False,
                    "think": False,
                    "options": {"num_predict": 8, "temperature": 0},
                    "messages": [
                        {"role": "system", "content": self._JUDGE_SYSTEM},
                        {
                            "role": "user",
                            "content": (
                                "<Instruct>: Given a query, retrieve the relevant passages\n"
                                f"<Query>: {query}\n"
                                f"<Document>: {chunk.text[:1000]}"
                            ),
                        },
                    ],
                }
                try:
                    r = await client.post(f"{self._base_url}/api/chat", json=payload)
                    r.raise_for_status()
                    content = (
                        ((r.json().get("message") or {}).get("content") or "")
                        .strip()
                        .lower()
                    )
                    if content.startswith("yes"):
                        return chunk, 1.0
                    if content.startswith("no"):
                        return chunk, 0.0
                    return chunk, 0.5
                except Exception:
                    return chunk, chunk.score

            results = await asyncio.gather(*(_judge(c) for c in to_score))

        # Sort by rerank score desc; tie-break on original retrieval score, then order.
        order = sorted(
            enumerate(results),
            key=lambda kv: (-kv[1][1], -kv[1][0].score, kv[0]),
        )
        return [
            ContextChunk(
                text=c.text,
                dataset=c.dataset,
                row_id=c.row_id,
                score=s,
                metadata={**(c.metadata or {}), "rerank_score": s, "original_score": c.score},
            )
            for _, (c, s) in order[:top_n]
        ]


def create_reranker(
    kind: str,
    *,
    model_name: str = "BAAI/bge-reranker-v2-m3",
    provider: Any = None,
    base_url: str = "",
    api_key: str | None = None,
) -> BaseReranker:
    """Factory: create a reranker by kind string.

    Kinds: ``none`` (passthrough), ``cross-encoder`` (HF, needs model cache),
    ``llm`` (1-10 scoring via the shared LLM provider), ``ollama`` (Qwen3-Reranker
    yes/no judge via an ollama endpoint — the lightweight default).
    """
    if kind == "none" or not kind:
        return NoopReranker()
    if kind == "cross-encoder":
        return CrossEncoderReranker(model_name=model_name)
    if kind == "llm":
        if provider is None:
            logger.warning("LLM reranker requires a provider, falling back to noop")
            return NoopReranker()
        return LLMReranker(provider=provider)
    if kind == "ollama":
        if not base_url:
            logger.warning("Ollama reranker requires base_url, falling back to noop")
            return NoopReranker()
        return OllamaReranker(model_name=model_name, base_url=base_url, api_key=api_key)
    logger.warning("Unknown reranker kind '%s', falling back to noop", kind)
    return NoopReranker()
