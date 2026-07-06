"""Context window management for RAG pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

import pyarrow as pa

logger = logging.getLogger(__name__)

try:
    import tiktoken

    _has_tiktoken = True
except ImportError:
    _has_tiktoken = False


@lru_cache(maxsize=16)
def _get_encoding(model: str) -> tiktoken.Encoding | None:
    """Get cached tiktoken encoding for a model."""
    if not _has_tiktoken:
        return None
    try:
        return tiktoken.encoding_for_model(model)
    except (KeyError, ValueError):
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    """Count tokens in text using tiktoken, or fall back to heuristic."""
    encoding = _get_encoding(model)
    if encoding is not None:
        return len(encoding.encode(text))
    # Heuristic: CJK ~1.5 chars/token, ASCII ~4 chars/token
    try:
        from arrow_lake.query._chinese_tokenizer import has_cjk

        if has_cjk(text):
            return int(len(text) / 1.5)
    except ImportError:
        pass
    return len(text) // 4


@dataclass(frozen=True)
class ContextChunk:
    """A single chunk of context from a retrieval result."""

    text: str
    dataset: str
    row_id: str
    score: float
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class ContextCitation:
    """Citation reference for a context chunk.

    Also re-exported as RAGCitation in pipeline.py for backward compatibility.
    """

    chunk_index: int
    dataset: str
    row_id: str
    score: float
    text_excerpt: str


@dataclass(frozen=True)
class FeedbackEntry:
    """User feedback on a RAG response turn."""

    session_id: str
    turn_id: int
    rating: str
    flagged_citation_indices: tuple[int, ...]
    comment: str = ""
    timestamp: float = 0.0


class ContextWindow:
    """Token-budget-aware context window for RAG assembly.

    Manages deduplication by (dataset, row_id) and truncates
    chunks that exceed the remaining token budget.
    """

    def __init__(
        self,
        token_budget: int,
        max_chunks: int | None = None,
    ) -> None:
        self._token_budget = token_budget
        self._max_chunks = max_chunks
        self._chunks: list[ContextChunk] = []
        self._seen: set[tuple[str, str]] = set()
        self._current_tokens = 0

    @property
    def token_count(self) -> int:
        return self._current_tokens

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    @property
    def citations(self) -> list[ContextCitation]:
        return [
            ContextCitation(
                chunk_index=i,
                dataset=chunk.dataset,
                row_id=chunk.row_id,
                score=chunk.score,
                text_excerpt=chunk.text[:100],
            )
            for i, chunk in enumerate(self._chunks)
        ]

    def add_chunk(self, chunk: ContextChunk, *, skip_dedup: bool = False) -> bool:
        """Add a chunk to the window (collects all, call finalize() to apply budget).

        Args:
            chunk: The context chunk to add.
            skip_dedup: If True, bypass deduplication check (used for
                synthetic chunks like graph context).

        Returns:
            True if the chunk was added, False if it was a duplicate.
        """
        # Dedup check
        if not skip_dedup:
            key = (chunk.dataset, chunk.row_id)
            if key in self._seen:
                return False

        self._chunks.append(chunk)
        if not skip_dedup:
            self._seen.add((chunk.dataset, chunk.row_id))
        return True

    def finalize(self) -> None:
        """Sort chunks by score descending, then apply token budget."""
        if not self._chunks:
            return

        # Sort by score descending
        self._chunks.sort(key=lambda c: c.score, reverse=True)

        # Apply token budget in score order
        trimmed: list[ContextChunk] = []
        tokens = 0
        for chunk in self._chunks:
            chunk_tokens = count_tokens(chunk.text)
            if tokens + chunk_tokens <= self._token_budget:
                trimmed.append(chunk)
                tokens += chunk_tokens
            else:
                # Try truncated version at sentence boundary
                remaining = self._token_budget - tokens
                if remaining <= 0:
                    break
                truncated_text = _truncate_at_sentence(chunk.text, remaining * 4)
                actual_tokens = count_tokens(truncated_text)
                if actual_tokens > 0 and actual_tokens <= remaining:
                    trimmed.append(ContextChunk(
                        text=truncated_text,
                        dataset=chunk.dataset,
                        row_id=chunk.row_id,
                        score=chunk.score,
                        metadata=chunk.metadata,
                    ))
                    tokens += actual_tokens
                break  # Budget exhausted

        if self._max_chunks is not None:
            trimmed = trimmed[:self._max_chunks]

        self._chunks = trimmed
        self._current_tokens = tokens

    def assemble(self) -> str:
        """Render context chunks into a single string with citation markers.

        Graph-type chunks (dataset == 'knowledge_graph') are placed first
        under a dedicated section header, followed by document chunks.
        When no graph chunks are present, the original numbered format is
        preserved for backward compatibility.
        """
        if not self._chunks:
            return ""

        # Check if there are any graph chunks
        has_graph = any(c.dataset == "knowledge_graph" for c in self._chunks)

        if not has_graph:
            # Preserve original numbered format for pure text contexts
            parts: list[str] = []
            for i, chunk in enumerate(self._chunks, start=1):
                parts.append(f"[{i}] {chunk.text}")
            return "\n\n".join(parts)

        # Mixed context: separate graph and document sections
        graph_parts: list[str] = []
        text_parts: list[str] = []
        text_index = 0

        for chunk in self._chunks:
            if chunk.dataset == "knowledge_graph":
                graph_parts.append(f"[knowledge_graph] {chunk.text}")
            else:
                text_index += 1
                text_parts.append(f"[{text_index}] {chunk.text}")

        sections: list[str] = []
        if graph_parts:
            sections.append("== Knowledge Graph Context ==\n" + "\n".join(graph_parts))
        if text_parts:
            sections.append("== Document Context ==\n" + "\n".join(text_parts))

        return "\n\n".join(sections)

    def add_graph_context(self, triplets_text: str) -> bool:
        """Add graph triplets as a synthetic 'knowledge_graph' chunk.

        Args:
            triplets_text: Serialized graph triplets (one per line).

        Returns:
            True if the chunk was added, False if text was empty or
            exceeded the budget.
        """
        if not triplets_text or not triplets_text.strip():
            return False
        chunk = ContextChunk(
            text=triplets_text,
            dataset="knowledge_graph",
            row_id="__graph_context__",
            score=1.0,
        )
        return self.add_chunk(chunk, skip_dedup=True)

    def clear(self) -> None:
        """Remove all chunks and reset token count."""
        self._chunks.clear()
        self._seen.clear()
        self._current_tokens = 0


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """Truncate text at a sentence boundary, preserving at least 50%."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    for sep in ("。", "！", "？", ".", "!", "?", "\n", "；", ";"):
        idx = truncated.rfind(sep)
        if idx > len(truncated) * 0.5:
            return truncated[:idx + 1]
    return truncated


def table_to_chunks(
    table: pa.Table,
    dataset_name: str,
    score_column: str | None = None,
    text_column: str = "text_content",
    row_id_column: str = "row_id",
    metadata_column: str | None = None,
) -> list[ContextChunk]:
    """Convert a PyArrow table of search results into ContextChunk list.

    Args:
        table: PyArrow table with search results.
        dataset_name: Name of the source dataset.
        score_column: Column name for relevance scores. If None, defaults to 1.0.
        text_column: Column name for text content.
        row_id_column: Column name for row identifiers.
        metadata_column: Optional column name for metadata dicts.

    Returns:
        List of ContextChunk instances.
    """
    if table.num_rows == 0:
        return []

    # 自动探测文本列：显式 text_column 缺失时回退常见列名
    # （兼容 jsonl 摄入的 text_content / docling 摄入的 text 等）
    if text_column not in table.column_names:
        for _alt in ("text", "content", "page_content", "text_content"):
            if _alt in table.column_names:
                text_column = _alt
                break
        else:
            raise KeyError(
                f"文本列 '{text_column}' 不在 {dataset_name} schema "
                f"(已有列: {table.column_names})"
            )
    texts = table.column(text_column).to_pylist()
    if row_id_column in table.column_names:
        row_ids = table.column(row_id_column).to_pylist()
    else:
        row_ids = list(range(len(texts)))

    if score_column and score_column in table.column_names:
        scores = table.column(score_column).to_pylist()
    else:
        scores = [1.0] * len(texts)

    metadatas: list[dict[str, object] | None] | None = None
    if metadata_column and metadata_column in table.column_names:
        metadatas = table.column(metadata_column).to_pylist()

    chunks: list[ContextChunk] = []
    for i in range(len(texts)):
        meta = None
        if metadatas and metadatas[i] is not None:
            meta = dict(metadatas[i]) if isinstance(metadatas[i], dict) else None
        chunks.append(
            ContextChunk(
                text=str(texts[i]) if texts[i] is not None else "",
                dataset=dataset_name,
                row_id=str(row_ids[i]) if row_ids[i] is not None else str(i),
                score=float(scores[i]) if scores[i] is not None else 1.0,
                metadata=meta,
            )
        )
    return chunks
