"""Chinese text pre-tokenization for FTS.

Uses jieba to segment CJK text into space-separated tokens so that
lancedb's built-in space-based tokenizer can index and search Chinese
content correctly.
"""

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

_JIEBA_AVAILABLE = False
try:
    import jieba

    _JIEBA_AVAILABLE = True
except ImportError:
    pass

_CJK_PATTERN = re.compile(r"[一-鿿㐀-䶿豈-﫿]")


def has_cjk(text: str) -> bool:
    """Return True if text contains CJK characters."""
    return bool(_CJK_PATTERN.search(text))


def segment_text(text: str) -> str:
    """Segment Chinese text for FTS indexing.

    CJK characters are segmented by jieba; non-CJK portions
    (English words, numbers, punctuation) are preserved as-is.
    The result is a space-joined string suitable for lancedb's
    default tokenizer.

    Falls back to returning the original text unchanged if jieba
    is not installed or text contains no CJK characters.
    """
    if not _JIEBA_AVAILABLE or not text or not has_cjk(text):
        return text

    # jieba cuts sentence at CJK boundaries and preserves
    # ASCII words/punctuation as-is.  We then collapse
    # whitespace to single spaces.
    try:
        segments = jieba.lcut(text)
        return " ".join(segments)
    except (ValueError, RuntimeError, UnicodeDecodeError) as exc:
        _log.warning("jieba segmentation failed, returning original text", exc_info=True)
        return text


def segment_query(query: str) -> str:
    """Segment a search query, same rules as segment_text.

    For multi-word CJK queries this produces space-separated tokens
    that lancedb's default BM25 tokenizer can match against
    jieba-segmented indexed content.
    """
    return segment_text(query)
