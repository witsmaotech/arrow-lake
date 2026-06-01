"""Query transformation / expansion for RAG pipeline.

Provides pluggable query transformers that rewrite or expand user queries
before retrieval, improving recall for complex or ambiguous questions.

Three implementations:
- IdentityTransformer: passthrough (default, zero overhead)
- HyDETransformer: generate hypothetical answer, use for retrieval
- MultiQueryTransformer: generate multiple query variants, merge results
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class BaseQueryTransformer(ABC):
    """Abstract base class for query transformers."""

    @abstractmethod
    async def transform(self, question: str) -> list[str]:
        """Transform a question into one or more search queries."""


class IdentityTransformer(BaseQueryTransformer):
    """Passthrough — returns the original question unchanged."""

    async def transform(self, question: str) -> list[str]:
        return [question]


class HyDETransformer(BaseQueryTransformer):
    """Hypothetical Document Embedding (HyDE).

    Generates a hypothetical answer to the question, then uses that answer
    for retrieval. The answer tends to be closer to relevant documents
    in embedding space than the question itself.

    Args:
        provider: LLM provider for generating hypothetical answers.
        max_answer_tokens: Max tokens for the hypothetical answer.
    """

    _HYDE_PROMPT = (
        "Please write a detailed passage that answers the following question. "
        "Write as if you are an expert providing a thorough explanation.\n\n"
        "Question: {question}\n\nAnswer:"
    )

    def __init__(self, provider: Any, max_answer_tokens: int = 256) -> None:
        self._provider = provider
        self._max_answer_tokens = max_answer_tokens

    async def transform(self, question: str) -> list[str]:
        from arrow_lake.rag.provider import LLMMessage

        prompt = self._HYDE_PROMPT.format(question=question)
        try:
            resp = await self._provider.generate([
                LLMMessage(role="user", content=prompt),
            ])
            return [question, resp.content.strip()]
        except Exception:
            logger.warning("HyDE generation failed, using original query", exc_info=True)
            return [question]


class MultiQueryTransformer(BaseQueryTransformer):
    """Multi-query expansion.

    Generates multiple reformulations of the question from different angles,
    retrieves for each, and merges results. Improves recall for ambiguous
    or multi-faceted questions.

    Args:
        provider: LLM provider for generating query variants.
        num_variants: Number of query variants to generate (default 3).
    """

    _MULTI_QUERY_PROMPT = (
        "You are an AI language model assistant. Your task is to generate "
        "{num} different versions of the given user question to retrieve "
        "relevant documents from a vector database.\n\n"
        "Generate alternative questions that cover different aspects or "
        "phrasings. Output ONE question per line, nothing else.\n\n"
        "Original question: {question}\n\nAlternative questions:"
    )

    def __init__(self, provider: Any, num_variants: int = 3) -> None:
        self._provider = provider
        self._num_variants = num_variants

    async def transform(self, question: str) -> list[str]:
        from arrow_lake.rag.provider import LLMMessage

        prompt = self._MULTI_QUERY_PROMPT.format(
            num=self._num_variants, question=question,
        )
        try:
            resp = await self._provider.generate([
                LLMMessage(role="user", content=prompt),
            ])
            variants = [line.strip() for line in resp.content.strip().split("\n") if line.strip()]
            # Always include the original question
            return [question, *[v for v in variants if v != question][:self._num_variants]]
        except Exception:
            logger.warning("Multi-query generation failed, using original query", exc_info=True)
            return [question]


def create_query_transformer(
    kind: str,
    *,
    provider: Any = None,
    hyde_max_tokens: int = 256,
    multi_query_variants: int = 3,
) -> BaseQueryTransformer:
    """Factory: create a query transformer by kind string."""
    if kind == "none" or not kind or kind == "identity":
        return IdentityTransformer()
    if kind == "hyde":
        if provider is None:
            logger.warning("HyDE transformer requires a provider, falling back to identity")
            return IdentityTransformer()
        return HyDETransformer(provider=provider, max_answer_tokens=hyde_max_tokens)
    if kind == "multi_query":
        if provider is None:
            logger.warning("Multi-query transformer requires a provider, falling back to identity")
            return IdentityTransformer()
        return MultiQueryTransformer(provider=provider, num_variants=multi_query_variants)
    logger.warning("Unknown query_transform kind '%s', falling back to identity", kind)
    return IdentityTransformer()
