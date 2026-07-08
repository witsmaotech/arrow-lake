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
- DOCLING_HYBRID: docling 结构感知分块（HybridChunker，吃 DoclingDocument；需 docling extra）
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

# HybridChunker 来自 docling 主包（docling.chunking），仅在安装 docling extra 时可用。
_DOCLING_CHUNK_AVAILABLE = False
try:
    from docling.chunking import HybridChunker  # noqa: F401
    _DOCLING_CHUNK_AVAILABLE = True
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
        docling_chunk_tokenizer: HuggingFace model id for HybridChunker tokenizer
            (default "BAAI/bge-m3" — 与嵌入模型对齐；仅 DOCLING_HYBRID 策略使用)。
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
        docling_chunk_tokenizer: str = "BAAI/bge-m3",
    ) -> None:
        self._strategy = strategy
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._tokenizer = tokenizer
        self._embedding_model = embedding_model
        self._similarity_threshold = similarity_threshold
        self._min_chunk_size = min_chunk_size
        self._docling_chunk_tokenizer = docling_chunk_tokenizer

        self._chonkie_chunker: Any = None
        self._semchunk_tokenizer: Any = None
        self._docling_hybrid_chunker: Any = None

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
        elif self._strategy == ChunkStrategy.DOCLING_HYBRID and not _DOCLING_CHUNK_AVAILABLE:
            logger.warning(
                "docling HybridChunker not installed, falling back to RECURSIVE. "
                "Install with: pip install arrow-lake[docling]"
            )
            self._strategy = ChunkStrategy.RECURSIVE

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
        """Chunk text using semchunk hierarchical splitting.

        semchunk>=2.0 changed the signature: the ``tokenizer=`` kwarg was replaced
        by a required positional ``token_counter: Callable[[str], int]``. Build one
        from the resolved tokenizer (tiktoken ``Encoding`` → ``.encode``; HuggingFace
        → ``__call__``), falling back to a char counter when no tokenizer resolved.
        """
        import semchunk

        tokenizer = self._get_semchunk_tokenizer()
        if tokenizer is None:
            token_counter = len
        elif hasattr(tokenizer, "encode"):  # tiktoken Encoding
            token_counter = lambda s: len(tokenizer.encode(s))
        else:  # HuggingFace transformers tokenizer
            token_counter = lambda s: len(tokenizer(s)["input_ids"])
        return semchunk.chunk(text, chunk_size=self._chunk_size, token_counter=token_counter)

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

    def _get_docling_hybrid_chunker(self, docling_doc: Any) -> Any:
        """懒加载 docling HybridChunker（按 docling_doc 重建，因为 serializer 绑定文档）。"""
        from docling.chunking import HybridChunker
        from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
        from transformers import AutoTokenizer

        tokenizer = HuggingFaceTokenizer(
            tokenizer=AutoTokenizer.from_pretrained(self._docling_chunk_tokenizer),
        )
        return HybridChunker(tokenizer=tokenizer, merge_peers=True)

    def _chunk_with_docling_hybrid(self, docling_doc: Any) -> list[str]:
        """用 docling HybridChunker 对 DoclingDocument 做结构感知分块。

        HybridChunker 先按文档结构（标题/段落/列表/表格）切粗块，再做
        tokenization-aware 细化与过小块合并。contextualize() 返回带标题/题注
        上下文的增强文本（这才是要嵌入的文本）。
        """
        chunker = self._get_docling_hybrid_chunker(docling_doc)
        raw = list(chunker.chunk(dl_doc=docling_doc))
        return [chunker.contextualize(chunk=c) for c in raw]

    def _chunk_docling_hybrid(
        self, docling_doc: Any, pages: list[tuple[int, str]],
    ) -> list[Chunk]:
        """DOCLING_HYBRID 分发：有 DoclingDocument 走结构感知；无则降级 RECURSIVE。"""
        if docling_doc is None:
            logger.warning(
                "docling_hybrid strategy got no DoclingDocument (backend != docling?), "
                "degrading to RECURSIVE on extracted text",
            )
            fallback: list[Chunk] = []
            idx = 0
            for page_num, page_text in pages:
                if not page_text or not page_text.strip():
                    continue
                for part in _split_recursive(page_text, self._chunk_size, self._chunk_overlap):
                    if part.strip():
                        fallback.append(Chunk(text=part, page_number=page_num, chunk_index=idx))
                        idx += 1
            return fallback

        try:
            parts = self._chunk_with_docling_hybrid(docling_doc)
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            raise DocumentError(
                error_code=ErrorCode.DOCUMENT_CHUNK_FAILED,
                message=f"docling HybridChunker failed: {exc}",
            ) from exc

        chunks: list[Chunk] = []
        for idx, part in enumerate(parts):
            if part and part.strip():
                # 结构感知 chunk 无单一页码（可能跨页/跨节），用 0 表示 metadata-only
                chunks.append(Chunk(text=part, page_number=0, chunk_index=idx))
        logger.debug(
            "docling_hybrid_chunked doc_items=%s chunks=%d",
            getattr(docling_doc, "num_items", "?"), len(chunks),
        )
        return chunks

    def chunk(
        self,
        pages: list[tuple[int, str]],
        *,
        docling_doc: Any = None,
    ) -> list[Chunk]:
        """Chunk document pages into embedding-ready pieces.

        Each page is chunked independently to preserve page_number attribution.

        Args:
            pages: List of (page_number, page_text) tuples.
            docling_doc: DoclingDocument 对象（仅 DOCLING_HYBRID 策略消费）。
                由 docling 后端解析时透传；其他后端为 None。若策略为
                DOCLING_HYBRID 但未提供，降级为对 pages 文本做 RECURSIVE 切分。

        Returns:
            List of Chunk instances with sequential indices.

        Raises:
            DocumentError: If chunking fails unexpectedly.
        """
        # DOCLING_HYBRID 走结构感知路径，忽略 pages 分页（直接吃 DoclingDocument）
        if self._strategy == ChunkStrategy.DOCLING_HYBRID:
            return self._chunk_docling_hybrid(docling_doc, pages)

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
