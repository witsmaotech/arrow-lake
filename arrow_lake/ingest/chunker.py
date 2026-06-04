"""Document chunking strategies — page, paragraph, recursive, semchunk, chonkie.  # noqa: RUF001

Provides DocumentChunker for splitting extracted document text into
chunks suitable for embedding and RAG retrieval.

Strategies:
- PAGE: one chunk per page (no splitting)
- PARAGRAPH: split on double-newline boundaries
- RECURSIVE: sentence-aware recursive splitting (built-in, zero deps)
- SEMCHUNK: multi-level hierarchical splitting via semchunk (optional dep)
- CHONKIE_TOKEN: token-based splitting via chonkie (optional dep)
- CHONKIE_SEMANTIC: embedding-similarity splitting via chonkie (optional dep)
- CHONKIE_SDPM: semantic double-pass merge via chonkie (optional dep)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from arrow_lake.config._enums import ChunkStrategy
from arrow_lake.exceptions import DocumentError, ErrorCode

logger = logging.getLogger(__name__)

__all__ = ["Chunk", "DocumentChunker"]

_SEMCHUNK_AVAILABLE = False
try:
    import semchunk  # noqa: F401
    _SEMCHUNK_AVAILABLE = True
except ImportError:
    pass

_CHONKIE_AVAILABLE = False
try:
    import chonkie  # noqa: F401
    _CHONKIE_AVAILABLE = True
except ImportError:
    pass


@dataclass(frozen=True)
class Chunk:
    """A text chunk extracted from a document.

    Attributes:
        text: Chunk text content.
        page_number: Source page number (1-based, 0 for metadata-only).
        chunk_index: Sequential chunk index within the document.
        metadata: Additional metadata (source file, section, etc.).
    """

    text: str
    page_number: int = 0
    chunk_index: int = 0
    metadata: dict[str, object] | None = None


def _split_by_paragraph(text: str) -> list[str]:
    """Split text on double-newlines (paragraph boundaries)."""
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _split_recursive(text: str, size: int, overlap: int) -> list[str]:
    """Recursively split text respecting sentence boundaries.

    Tries to split on sentences first, then on words, to keep
    chunks semantically coherent.
    """
    if len(text) <= size:
        return [text.strip()] if text.strip() else []

    chunks: list[str] = []

    sentences = re.split(r"(?<=[.!?。！？])\s+", text)  # noqa: RUF001
    if len(sentences) <= 1:
        words = text.split()
        pos = 0
        while pos < len(words):
            end = pos + max(1, size // 4)
            chunk = " ".join(words[pos:end])
            chunks.append(chunk)
            pos = end - max(1, overlap // 4)
    else:
        current = ""
        for sentence in sentences:
            if not sentence.strip():
                continue
            if len(current) + len(sentence) + 1 > size and current:
                chunks.append(current.strip())
                if overlap > 0 and len(current) > overlap:
                    current = current[-overlap:] + " " + sentence
                else:
                    current = sentence
            else:
                current = current + " " + sentence if current else sentence
        if current.strip():
            chunks.append(current.strip())

    return [c for c in chunks if c.strip()]


class DocumentChunker:
    """Splits extracted document pages into embedding-ready chunks.

    Args:
        strategy: Chunking strategy from ChunkStrategy enum.
        chunk_size: Target chunk size in characters (or tokens for semchunk/chonkie).
        chunk_overlap: Overlap between consecutive chunks.
        tokenizer: Tokenizer identifier for semchunk (e.g. "cl100k_base").
        embedding_model: HuggingFace model for chonkie semantic/sdpm chunkers.
        similarity_threshold: Similarity threshold for chonkie semantic splitting.
        min_chunk_size: Minimum chunk size for chonkie SDPM merge phase.
    """

    _CHONKIE_STRATEGIES = frozenset({
        ChunkStrategy.CHONKIE_TOKEN,
        ChunkStrategy.CHONKIE_SEMANTIC,
        ChunkStrategy.CHONKIE_SDPM,
    })

    def __init__(
        self,
        *,
        strategy: ChunkStrategy = ChunkStrategy.RECURSIVE,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        tokenizer: str = "",
        embedding_model: str = "",
        similarity_threshold: float = 0.5,
        min_chunk_size: int = 100,
    ) -> None:
        self._strategy = strategy
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._tokenizer = tokenizer
        self._embedding_model = embedding_model
        self._similarity_threshold = similarity_threshold
        self._min_chunk_size = min_chunk_size

        self._chonkie_chunker: Any = None
        self._semchunk_tokenizer: Any = None

        self._validate_strategy()

    def _validate_strategy(self) -> None:
        """Check library availability and downgrade strategy if needed."""
        if self._strategy == ChunkStrategy.SEMCHUNK and not _SEMCHUNK_AVAILABLE:
            logger.warning(
                "semchunk not installed, falling back to RECURSIVE. "
                "Install with: pip install arrow-lake[chunking-advanced]"
            )
            self._strategy = ChunkStrategy.RECURSIVE
        elif self._strategy in self._CHONKIE_STRATEGIES and not _CHONKIE_AVAILABLE:
            logger.warning(
                "chonkie not installed, falling back to RECURSIVE. "
                "Install with: pip install arrow-lake[chunking-semantic]"
            )
            self._strategy = ChunkStrategy.RECURSIVE
        elif self._strategy in (
            ChunkStrategy.CHONKIE_SEMANTIC,
            ChunkStrategy.CHONKIE_SDPM,
        ) and not self._embedding_model:
            logger.warning(
                "%s requires embedding_model, falling back to CHONKIE_TOKEN",
                self._strategy.value,
            )
            self._strategy = ChunkStrategy.CHONKIE_TOKEN

    def _get_semchunk_tokenizer(self) -> Any:
        """Lazily resolve semchunk tokenizer (tiktoken > HuggingFace > None)."""
        if self._semchunk_tokenizer is not None or not self._tokenizer:
            return self._semchunk_tokenizer

        try:
            import tiktoken
            self._semchunk_tokenizer = tiktoken.get_encoding(self._tokenizer)
            return self._semchunk_tokenizer
        except (ImportError, KeyError):
            pass

        try:
            from transformers import AutoTokenizer
            self._semchunk_tokenizer = AutoTokenizer.from_pretrained(self._tokenizer, revision="main")  # nosec B615: revision pinned to main branch
            return self._semchunk_tokenizer
        except (ImportError, OSError):
            pass

        logger.warning(
            "Could not resolve tokenizer %r for semchunk, using char-based splitting",
            self._tokenizer,
        )
        return None

    def _chunk_with_semchunk(self, text: str) -> list[str]:
        """Chunk text using semchunk hierarchical splitting."""
        import semchunk

        tokenizer = self._get_semchunk_tokenizer()
        kwargs: dict[str, Any] = {"chunk_size": self._chunk_size}
        if tokenizer is not None:
            kwargs["tokenizer"] = tokenizer
        return semchunk.chunk(text, **kwargs)

    def _chunk_with_chonkie(self, text: str) -> list[str]:
        """Chunk text using the selected chonkie chunker (cached per instance)."""
        import chonkie

        if self._chonkie_chunker is None:
            if self._strategy == ChunkStrategy.CHONKIE_TOKEN:
                kwargs: dict[str, Any] = {
                    "chunk_size": self._chunk_size,
                    "chunk_overlap": self._chunk_overlap,
                }
                if self._tokenizer:
                    kwargs["tokenizer"] = self._tokenizer
                self._chonkie_chunker = chonkie.TokenChunker(**kwargs)
            elif self._strategy == ChunkStrategy.CHONKIE_SEMANTIC:
                self._chonkie_chunker = chonkie.SemanticChunker(
                    embedding_model=self._embedding_model,
                    threshold=self._similarity_threshold,
                    chunk_size=self._chunk_size,
                )
            elif self._strategy == ChunkStrategy.CHONKIE_SDPM:
                if hasattr(chonkie, "SDPMChunker"):
                    self._chonkie_chunker = chonkie.SDPMChunker(
                        embedding_model=self._embedding_model,
                        threshold=self._similarity_threshold,
                        chunk_size=self._chunk_size,
                    )
                else:
                    self._chonkie_chunker = chonkie.SemanticChunker(
                        embedding_model=self._embedding_model,
                        threshold=self._similarity_threshold,
                        chunk_size=self._chunk_size,
                    )

        result = self._chonkie_chunker.chunk(text)
        return [c.text for c in result]

    def chunk(self, pages: list[tuple[int, str]]) -> list[Chunk]:
        """Chunk document pages into embedding-ready pieces.

        Each page is chunked independently to preserve page_number attribution.

        Args:
            pages: List of (page_number, page_text) tuples.

        Returns:
            List of Chunk instances with sequential indices.

        Raises:
            DocumentError: If chunking fails unexpectedly.
        """
        chunks: list[Chunk] = []
        idx = 0

        try:
            for page_num, page_text in pages:
                if not page_text or not page_text.strip():
                    continue

                if self._strategy == ChunkStrategy.PAGE:
                    parts = [page_text.strip()]
                elif self._strategy == ChunkStrategy.PARAGRAPH:
                    parts = _split_by_paragraph(page_text)
                elif self._strategy == ChunkStrategy.SEMCHUNK:
                    parts = self._chunk_with_semchunk(page_text)
                elif self._strategy in self._CHONKIE_STRATEGIES:
                    parts = self._chunk_with_chonkie(page_text)
                else:
                    parts = _split_recursive(
                        page_text, self._chunk_size, self._chunk_overlap,
                    )

                for part in parts:
                    if not part.strip():
                        continue
                    chunks.append(Chunk(
                        text=part,
                        page_number=page_num,
                        chunk_index=idx,
                    ))
                    idx += 1

        except DocumentError:
            raise
        except (TypeError, ValueError, ImportError) as exc:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_CHUNK_FAILED,
                message=f"Failed to chunk document: {exc}",
            ) from exc

        logger.debug(
            "document_chunked strategy=%s pages=%d chunks=%d",
            self._strategy.value,
            len(pages),
            len(chunks),
        )
        return chunks
