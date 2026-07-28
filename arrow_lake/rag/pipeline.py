"""RAG pipeline orchestration."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from arrow_lake.config import RAGConfig
from arrow_lake.rag.context import ContextChunk, ContextCitation, ContextWindow, count_tokens, table_to_chunks
from arrow_lake.rag.plan import RAGQueryPlan, resolve_score_column
from arrow_lake.rag.prompt import PromptRegistry
from arrow_lake.rag.provider import BaseLLMProvider, LLMMessage
from arrow_lake.rag.query_transform import (
    BaseQueryTransformer,
    IdentityTransformer,
    create_query_transformer,
)
from arrow_lake.rag.reranker import NoopReranker
from arrow_lake.rag.session import SessionStore
from arrow_lake.exceptions import ErrorCode, RAGError

logger = logging.getLogger(__name__)

PROMPT_INJECTION_RE = re.compile(
    r"(?i)("
    r"ignore previous|ignore above|ignore all|ignore everything|"
    r"new instructions?|system prompt|"
    r"you are now|act as|pretend you are|"
    r"disregard|override previous|"
    r"jailbreak|DAN mode|"
    r"output your instructions|reveal your prompt|repeat the above"
    r")"
)

# Alias: RAGCitation is the public name, ContextCitation is the internal name.
RAGCitation = ContextCitation


@dataclass(frozen=True)
class LatencyBreakdown:
    """Per-stage latency breakdown for a RAG query."""

    retrieval_ms: float
    context_ms: float
    llm_ms: float
    total_ms: float


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
    latency_breakdown: LatencyBreakdown | None = None
    verification: Any | None = None  # v1.9.6 P0-1 VerificationResult (None if disabled)


# Type alias for the retriever callback.
# (question, dataset_name, top_k, strategy) -> Arrow Table.
# ``strategy`` is the effective retrieval strategy ("fts"/"vector"/"hybrid"),
# resolved by the pipeline from the per-query override or config default, and
# forwarded so the retriever can dispatch to the right backend. Retriever
# implementations MUST accept the 4th positional arg.
RetrieverFunc = Callable[
    [str, str, int, str],
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
        reranker: Any | None = None,
    ) -> None:
        self._llm = llm_provider
        self._config = config
        self._retriever = retriever
        self._context_window_tokens = context_window_tokens
        self._registry = prompt_registry or PromptRegistry()
        self._session_store = session_store
        self._reranker = reranker
        self._query_transformer: BaseQueryTransformer | None = None

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
        history: list[dict] | None = None,
    ) -> list[LLMMessage]:
        """Build the message list for the LLM."""
        messages: list[LLMMessage] = []

        def _sanitize(text: str) -> str:
            return PROMPT_INJECTION_RE.sub("[FILTERED]", text)

        # System prompt
        system = self._config.system_prompt
        if system:
            messages.append(LLMMessage(role="system", content=system))

        # Multi-turn history injection
        if history and self._config.history_injection_enabled:
            history_budget = int(self._context_window_tokens * self._config.history_budget_ratio)
            max_turns = self._config.history_max_turns
            used_tokens = 0
            for turn in history[-max_turns:]:
                q_tokens = count_tokens(turn.get("question", ""))
                a_tokens = count_tokens(turn.get("answer", ""))
                if used_tokens + q_tokens + a_tokens > history_budget:
                    break
                messages.append(LLMMessage(role="user", content=_sanitize(turn["question"])))
                messages.append(LLMMessage(role="assistant", content=turn["answer"]))
                used_tokens += q_tokens + a_tokens

        # Get the prompt template
        template_name = template_name or "default_qa"
        template = self._registry.get(template_name)
        safe_context = _sanitize(context_text)
        if template is None:
            # Fallback to raw context + question
            prompt = f"Context:\n{safe_context}\n\nQuestion: {_sanitize(question)}\n\nAnswer:"
        else:
            prompt = template.render(context=safe_context, question=_sanitize(question))

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
        strategy: str = "fts",
    ) -> pa.Table:
        """Run retrieval in a thread pool (DuckDB queries are synchronous).

        ``strategy`` is forwarded to the retriever so it can dispatch to
        fts / vector / hybrid backends. Defaults to "fts" for backward
        compatibility with retrievers that ignore it.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._retriever, question, dataset_name, top_k, strategy
        )

    def _get_query_transformer(self) -> BaseQueryTransformer:
        """Lazy-init query transformer from config."""
        if self._query_transformer is None:
            kind = self._config.query_transform
            if kind and kind != "none":
                self._query_transformer = create_query_transformer(
                    kind,
                    provider=self._llm,
                    hyde_max_tokens=self._config.hyde_max_tokens,
                    multi_query_variants=self._config.multi_query_variants,
                )
            else:
                self._query_transformer = IdentityTransformer()
        return self._query_transformer

    async def _retrieve_and_build_context(
        self,
        question: str,
        dataset_name: str,
        top_k: int,
        strategy: str | None = None,
    ) -> tuple[ContextWindow, str]:
        """Retrieve documents, rerank, and build context window. Returns (window, context_text)."""
        # Resolve the retrieval intent ONCE into a plan (strategy None→default,
        # top_k None→default), then pass it down as data instead of smuggling
        # strategy as a positional arg through every hop. (架构评审 #1)
        plan = RAGQueryPlan.resolve(question, dataset_name, top_k, strategy, self._config)

        chunks = await self._retrieve_ranked(plan)

        window = self._build_context_window()
        for chunk in chunks:
            window.add_chunk(chunk)
        window.finalize()

        if window.chunk_count == 0:
            raise RAGError(
                error_code=ErrorCode.RAG_RETRIEVAL_FAILED,
                message="Retrieval returned no relevant documents for the given query",
                context={"question": question, "dataset": dataset_name},
            )

        context_text = window.assemble()
        return window, context_text

    async def _retrieve_ranked(self, plan: RAGQueryPlan) -> list[ContextChunk]:
        """Run the retrieval stage end-to-end and return reranked chunks.

        Deep retrieval stage (架构评审 #1): multi-query fan-out → merge →
        score-column resolution → dedup → rerank. Backend dispatch + score
        normalization + rerank live here, behind one resolved plan — callers
        (_retrieve_and_build_context, and any future RetrievalStage consumer)
        never branch on strategy or hardcode score-column names.
        """
        transformer = self._get_query_transformer()
        queries = await transformer.transform(plan.question)

        # Parallel retrieval for each query variant, then merge
        if len(queries) == 1:
            result_table = await self._retrieve(
                queries[0], plan.dataset_name, plan.top_k, plan.strategy,
            )
        else:
            tables = await asyncio.gather(
                *(self._retrieve(q, plan.dataset_name, plan.top_k, plan.strategy)
                  for q in queries)
            )
            result_table = self._merge_tables(tables)

        score_column = resolve_score_column(plan.strategy, result_table.column_names)

        chunks = table_to_chunks(
            result_table,
            dataset_name=plan.dataset_name,
            score_column=score_column,
        )

        # Deduplicate by row_id across query variants
        chunks = self._deduplicate_chunks(chunks)

        # Rerank before context assembly. BaseReranker.rerank is uniformly async
        # (sync adapters just return without awaiting) — await unconditionally.
        # No per-caller iscoroutine bridge: one contract, one place.
        reranker = self._reranker or NoopReranker()
        return await reranker.rerank(plan.question, chunks, plan.rerank_top_n)

    # ------------------------------------------------------------------
    # Template-method hooks (overridden by GraphRAGPipeline)
    # ------------------------------------------------------------------

    def _extra_context_task(
        self, question: str, dataset_name: str, use_kg: bool
    ) -> Awaitable[str] | None:
        """Hook: return an awaitable yielding extra context text (e.g. graph
        triplets) to fuse into the vector context, or None to skip.

        The awaitable runs in PARALLEL with vector retrieval (asyncio.gather),
        so subclasses can overlap an expensive side retrieval (entity
        extraction + graph query) with the vector pass. Base: None.
        """
        return None

    def _fuse_extra_context(self, context_text: str, extra_text: str) -> str:
        """Hook: fuse extra context into the vector context.

        Base: identity (returns vector context unchanged). GraphRAG overrides
        with weighted graph/document fusion. Only called when
        ``_extra_context_task`` returned non-empty text.
        """
        return context_text

    @staticmethod
    def _merge_tables(tables: tuple[pa.Table, ...]) -> pa.Table:
        """Concatenate multiple retrieval result tables."""
        non_empty = [t for t in tables if t.num_rows > 0]
        if not non_empty:
            return tables[0] if tables else pa.table({})
        if len(non_empty) == 1:
            return non_empty[0]
        return pa.concat_tables(non_empty, promote_options="default")

    @staticmethod
    def _deduplicate_chunks(chunks: list[Any]) -> list[Any]:
        """Deduplicate chunks by (dataset, row_id), keeping highest score."""
        seen: dict[tuple[str, str], Any] = {}
        for chunk in chunks:
            key = (chunk.dataset, chunk.row_id)
            if key not in seen or chunk.score > seen[key].score:
                seen[key] = chunk
        return list(seen.values())

    async def query(
        self,
        question: str,
        dataset_name: str,
        *,
        top_k: int | None = None,
        strategy: str | None = None,
        template_name: str | None = None,
        session_id: str | None = None,
        use_kg: bool = True,
    ) -> RAGResponse:
        """Run a full RAG query: retrieve → context → generate.

        ``use_kg`` is accepted for signature parity with GraphRAGPipeline but
        ignored here (base pipeline has no KG to inject).
        """
        start = time.perf_counter()

        effective_top_k = top_k or self._config.default_top_k

        # 0. Load session history for multi-turn
        history: list[dict] | None = None
        if session_id and self._session_store and self._config.history_injection_enabled:
            history = self._session_store.get_history(session_id)

        # 1. Retrieval — vector retrieval runs in PARALLEL with any extra
        # context a subclass injects via _extra_context_task (GraphRAG:
        # entity extraction + graph triplets). Base pipeline returns None,
        # so this collapses to plain vector retrieval.
        extra_task = self._extra_context_task(question, dataset_name, use_kg)
        if extra_task is None:
            window, context_text = await self._retrieve_and_build_context(
                question, dataset_name, effective_top_k, strategy,
            )
        else:
            # Parallel vector + side retrieval (e.g. graph). If either fails,
            # cancel the other so we don't leak an in-flight graph/entity-
            # extraction coroutine — preserves the explicit graph_task.cancel()
            # the old GraphRAG.query() did on vector failure.
            vector_t = asyncio.ensure_future(
                self._retrieve_and_build_context(
                    question, dataset_name, effective_top_k, strategy,
                )
            )
            extra_t = asyncio.ensure_future(extra_task)
            try:
                (window, context_text), extra_text = await asyncio.gather(
                    vector_t, extra_t,
                )
            except BaseException:
                vector_t.cancel()
                extra_t.cancel()
                raise
            if extra_text:
                context_text = self._fuse_extra_context(context_text, extra_text)
        t_retrieval = time.perf_counter()

        # 2. Context assembly + message build
        messages = self._build_messages(question, context_text, template_name, history=history)
        t_context = time.perf_counter()

        # 3. LLM generation
        llm_response = await self._llm.generate(messages)
        t_llm = time.perf_counter()

        elapsed = (t_llm - start) * 1000
        citations = self._extract_citations(window)

        # v1.9.6 P0-1: faithfulness verification (lightweight [n] ref check).
        verification = None
        if getattr(self._config, "enable_verification", False):
            try:
                from arrow_lake.rag.verifier import verify
                verification = verify(llm_response.content, window.chunk_count)
            except Exception:  # noqa: BLE001 — never fail the query on verification
                logger.warning("rag_verification_failed", exc_info=True)

        breakdown = LatencyBreakdown(
            retrieval_ms=round((t_retrieval - start) * 1000, 1),
            context_ms=round((t_context - t_retrieval) * 1000, 1),
            llm_ms=round((t_llm - t_context) * 1000, 1),
            total_ms=round(elapsed, 1),
        )

        result = RAGResponse(
            answer=llm_response.content,
            citations=citations,
            retrieval_count=window.chunk_count,
            context_tokens=window.token_count if window.token_count > 0 else None,
            llm_usage=llm_response.usage,
            latency_ms=round(elapsed, 1),
            session_id=session_id,
            latency_breakdown=breakdown,
            verification=verification,
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

    async def query_stream_rich(
        self,
        question: str,
        dataset_name: str,
        *,
        top_k: int | None = None,
        strategy: str | None = None,
        template_name: str | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Stream RAG with rich events for front-end citation/latency display.

        v1.9.6 P1-9: yields ``("citations", tuple)`` first, ``("content", str)`` ×N,
        then ``("done", dict)`` with retrieval_count/context_tokens/latency_ms.
        """
        start = time.perf_counter()
        effective_top_k = top_k or self._config.default_top_k
        window, context_text = await self._retrieve_and_build_context(
            question, dataset_name, effective_top_k, strategy,
        )
        citations = self._extract_citations(window)
        yield ("citations", citations)
        messages = self._build_messages(question, context_text, template_name)
        async for chunk in self._llm.generate_stream(messages):
            yield ("content", chunk)
        yield ("done", {
            "retrieval_count": window.chunk_count,
            "context_tokens": window.token_count if window.token_count > 0 else None,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        })

    async def extract_entities(
        self,
        dataset_name: str,
        *,
        text_column: str = "text_content",
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
        window.finalize()

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

    async def batch_query(
        self,
        questions: list[str],
        dataset_name: str,
        *,
        top_k: int | None = None,
        strategy: str | None = None,
        concurrency: int = 5,
    ) -> list[RAGResponse]:
        """Concurrent batch RAG query with semaphore-limited fan-out."""
        sem = asyncio.Semaphore(max(concurrency, 1))  # 防 concurrency<=0 永久死锁

        async def _single(idx: int, q: str) -> RAGResponse:
            async with sem:
                return await self.query(q, dataset_name, top_k=top_k, strategy=strategy)

        tasks = [_single(i, q) for i, q in enumerate(questions)]
        return list(await asyncio.gather(*tasks))

    async def batch_query_stream(
        self,
        questions: list[str],
        dataset_name: str,
        *,
        top_k: int | None = None,
        strategy: str | None = None,
    ) -> AsyncIterator[tuple[int, str]]:
        """Stream batch: yields (question_index, chunk) interleaved."""
        sem = asyncio.Semaphore(len(questions))

        async def _stream_single(idx: int, q: str):
            async with sem:
                async for chunk in self.query_stream(q, dataset_name, top_k=top_k, strategy=strategy):
                    yield idx, chunk

        async def _merge():
            streams = [_stream_single(i, q) for i, q in enumerate(questions)]
            task_to_stream: dict[asyncio.Task, Any] = {}
            pending: set[asyncio.Task] = set()
            try:
                for s in streams:
                    t = asyncio.create_task(s.__anext__())
                    task_to_stream[t] = s
                    pending.add(t)
                while pending:
                    done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        pending.discard(task)
                        stream = task_to_stream.pop(task)
                        try:
                            idx, chunk = task.result()
                            next_task = asyncio.create_task(stream.__anext__())
                            task_to_stream[next_task] = stream
                            pending.add(next_task)
                            yield idx, chunk
                        except StopAsyncIteration:
                            pass  # stream exhausted
                yield -1, ""  # sentinel
            finally:
                # 消费者提前中断(break/异常)→ cancel 未完成 task,避免 LLM 流资源泄漏
                for t in pending:
                    t.cancel()

        async for item in _merge():
            if item[0] == -1:
                break
            yield item
