"""RAG pipeline orchestration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import pyarrow as pa

from arrow_lake.config import RAGConfig
from arrow_lake.rag.context import ContextCitation, ContextWindow, table_to_chunks
from arrow_lake.rag.prompt import PromptRegistry
from arrow_lake.rag.provider import BaseLLMProvider, LLMMessage
from arrow_lake.rag.session import SessionStore

logger = logging.getLogger(__name__)

# Alias: RAGCitation is the public name, ContextCitation is the internal name.
RAGCitation = ContextCitation


@dataclass(frozen=True)
class RAGResponse:
    """Response from the RAG pipeline."""

    answer: str
    citations: tuple[RAGCitation, ...]
    retrieval_count: int
    context_tokens: int | None = None
    llm_usage: dict[str, int] | None = None
    latency_ms: float | None = None
    session_id: str | None = None


# Type alias for the retriever callback
RetrieverFunc = Callable[
    [str, str, int],
    pa.Table,
]


class RAGPipeline:
    """Orchestrates retrieval → context assembly → LLM generation.

    The pipeline is agnostic to how retrieval works — it accepts a
    retriever callback that returns a PyArrow table.
    """

    def __init__(
        self,
        llm_provider: BaseLLMProvider,
        config: RAGConfig,
        retriever: RetrieverFunc,
        *,
        context_window_tokens: int = 4096,
        prompt_registry: PromptRegistry | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        self._llm = llm_provider
        self._config = config
        self._retriever = retriever
        self._context_window_tokens = context_window_tokens
        self._registry = prompt_registry or PromptRegistry()
        self._session_store = session_store

    def _build_context_window(self) -> ContextWindow:
        """Create a ContextWindow based on RAG config."""
        context_budget = int(
            self._config.context_budget_ratio
            * self._context_window_tokens
        )
        return ContextWindow(
            token_budget=context_budget,
            max_chunks=self._config.max_context_chunks,
        )

    def _build_messages(
        self,
        question: str,
        context_text: str,
        template_name: str | None = None,
    ) -> list[LLMMessage]:
        """Build the message list for the LLM."""
        messages: list[LLMMessage] = []

        # System prompt
        system = self._config.system_prompt
        if system:
            messages.append(LLMMessage(role="system", content=system))

        # Get the prompt template
        template_name = template_name or "default_qa"
        template = self._registry.get(template_name)
        if template is None:
            # Fallback to raw context + question
            prompt = f"Context:\n{context_text}\n\nQuestion: {question}\n\nAnswer:"
        else:
            prompt = template.render(context=context_text, question=question)

        messages.append(LLMMessage(role="user", content=prompt))
        return messages

    def _extract_citations(self, context_window: ContextWindow) -> tuple[RAGCitation, ...]:
        """Extract citations from the context window."""
        if not self._config.enable_citations:
            return ()
        return tuple(
            RAGCitation(
                chunk_index=c.chunk_index,
                dataset=c.dataset,
                row_id=c.row_id,
                score=c.score,
                text_excerpt=c.text_excerpt,
            )
            for c in context_window.citations
        )

    async def _retrieve(
        self,
        question: str,
        dataset_name: str,
        top_k: int,
    ) -> pa.Table:
        """Run retrieval in a thread pool (DuckDB queries are synchronous)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._retriever, question, dataset_name, top_k
        )

    async def _retrieve_and_build_context(
        self,
        question: str,
        dataset_name: str,
        top_k: int,
        strategy: str | None = None,
    ) -> tuple[ContextWindow, str]:
        """Retrieve documents and build context window. Returns (window, context_text)."""
        result_table = await self._retrieve(question, dataset_name, top_k)

        score_column = "_rrf_score" if strategy == "hybrid" else "_score"
        if score_column not in result_table.column_names:
            score_column = None

        chunks = table_to_chunks(
            result_table,
            dataset_name=dataset_name,
            score_column=score_column,
        )
        window = self._build_context_window()
        for chunk in chunks:
            window.add_chunk(chunk)

        context_text = window.assemble() if window.chunk_count > 0 else ""
        return window, context_text

    async def query(
        self,
        question: str,
        dataset_name: str,
        *,
        top_k: int | None = None,
        strategy: str | None = None,
        template_name: str | None = None,
        session_id: str | None = None,
    ) -> RAGResponse:
        """Run a full RAG query: retrieve → context → generate."""
        start = time.perf_counter()

        effective_top_k = top_k or self._config.default_top_k

        # 1-2. Retrieve and build context
        window, context_text = await self._retrieve_and_build_context(
            question, dataset_name, effective_top_k, strategy,
        )

        # 3. Build messages and call LLM
        messages = self._build_messages(question, context_text, template_name)
        llm_response = await self._llm.generate(messages)

        elapsed = (time.perf_counter() - start) * 1000
        citations = self._extract_citations(window)

        result = RAGResponse(
            answer=llm_response.content,
            citations=citations,
            retrieval_count=window.chunk_count,
            context_tokens=window.token_count if window.token_count > 0 else None,
            llm_usage=llm_response.usage,
            latency_ms=round(elapsed, 1),
            session_id=session_id,
        )

        # Persist turn in session store
        if self._session_store and session_id:
            self._session_store.save_turn(session_id, question, result)

        return result

    async def query_stream(
        self,
        question: str,
        dataset_name: str,
        *,
        top_k: int | None = None,
        strategy: str | None = None,
        template_name: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream a RAG query response."""
        effective_top_k = top_k or self._config.default_top_k

        # 1-2. Retrieve and build context
        _, context_text = await self._retrieve_and_build_context(
            question, dataset_name, effective_top_k, strategy,
        )

        # 3. Build messages and stream from LLM
        messages = self._build_messages(question, context_text, template_name)
        async for chunk in self._llm.generate_stream(messages):
            yield chunk

    async def extract_entities(
        self,
        dataset_name: str,
        *,
        text_column: str = "text",
        top_k: int | None = None,
        template_name: str | None = None,
    ) -> RAGResponse:
        """Extract entities from a dataset using the entity_extract template."""
        start = time.perf_counter()

        effective_top_k = top_k or self._config.default_top_k

        # Retrieve a sample of documents
        result_table = await self._retrieve(
            f"entity extraction from {dataset_name}",
            dataset_name,
            effective_top_k,
        )

        # Build context from all retrieved documents
        chunks = table_to_chunks(
            result_table,
            dataset_name=dataset_name,
            text_column=text_column,
            score_column=None,
        )
        window = self._build_context_window()
        for chunk in chunks:
            window.add_chunk(chunk)

        # Combine all text for entity extraction
        all_text = "\n\n".join(c.text for c in chunks) if chunks else ""

        # Get template
        tmpl_name = template_name or "entity_extract"
        template = self._registry.get(tmpl_name)
        if template is None:
            prompt = f"Extract entities from:\n{all_text}\n\nEntities:"
        else:
            prompt = template.render(text=all_text)

        messages: list[LLMMessage] = []
        if self._config.system_prompt:
            messages.append(LLMMessage(role="system", content=self._config.system_prompt))
        messages.append(LLMMessage(role="user", content=prompt))

        llm_response = await self._llm.generate(messages)

        elapsed = (time.perf_counter() - start) * 1000
        return RAGResponse(
            answer=llm_response.content,
            citations=(),
            retrieval_count=window.chunk_count,
            context_tokens=window.token_count if window.token_count > 0 else None,
            llm_usage=llm_response.usage,
            latency_ms=round(elapsed, 1),
        )
