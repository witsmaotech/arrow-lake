"""Chinese text pre-tokenization for FTS.

Uses jieba to segment CJK text into space-separated tokens so that
lancedb's built-in space-based tokenizer can index and search Chinese
content correctly.
"""

from __future__ import annotations

import logging
import re
import warnings

_log = logging.getLogger(__name__)

_JIEBA_AVAILABLE = False
try:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")
        import jieba

    _JIEBA_AVAILABLE = True
except ImportError:
    pass

# Lindera for Japanese morphological segmentation (v1.8.0 #4 — i18n).
_LINDERA_AVAILABLE = False
try:
    from lindera import Tokenizer as _LinderaTokenizer

    _LINDERA_AVAILABLE = True
except ImportError:
    pass

_CJK_PATTERN = re.compile(r"[一-鿿㐀-䶿豈-﫿]")


def has_cjk(text: str) -> bool:
    """Return True if text contains CJK characters."""
    return bool(_CJK_PATTERN.search(text))


# Hiragana (ぁ-ゟ) + Katakana (゠-ヿ) — Japanese kana, distinct from CJK ideographs.
_JP_PATTERN = re.compile("[ぁ-ヿ]")


def has_japanese(text: str) -> bool:
    """Return True if text contains Japanese Hiragana/Katakana."""
    return bool(_JP_PATTERN.search(text))


def segment_text(text: str) -> str:
    """Segment CJK/Japanese text for FTS indexing (v1.8.0 #4 — i18n).

    Routing: Japanese (Hiragana/Katakana) → lindera; Chinese (CJK ideographs)
    → jieba; other text preserved as-is. Output is space-joined, suitable for
    lancedb's default space-based tokenizer. Falls back to the original text
    if the relevant segmenter is not installed or fails.
    """
    if not text:
        return text

    # Japanese (kana) → lindera
    if has_japanese(text):
        if not _LINDERA_AVAILABLE:
            _log.warning(
                "Cannot segment Japanese text — lindera is not installed. "
                "Install with: pip install lindera"
            )
            return text
        try:
            tokenizer = _LinderaTokenizer()
            tokens = tokenizer.tokenize(text)
            return " ".join(
                getattr(t, "text", None) or getattr(t, "surface", "") or str(t)
                for t in tokens
            )
        except (ValueError, RuntimeError, UnicodeDecodeError, TypeError):
            _log.warning("lindera segmentation failed, returning original text", exc_info=True)
            return text

    # Chinese (CJK ideographs) → jieba
    if has_cjk(text):
        if not _JIEBA_AVAILABLE:
            _log.warning(
                "Cannot segment CJK text — jieba is not installed. "
                "Install with: pip install jieba"
            )
            return text
        try:
            return " ".join(jieba.lcut(text))
        except (ValueError, RuntimeError, UnicodeDecodeError):
            _log.warning("jieba segmentation failed, returning original text", exc_info=True)
            return text

    return text


def segment_query(query: str) -> str:
    """Segment a search query, same rules as segment_text.

    For multi-word CJK queries this produces space-separated tokens
    that lancedb's default BM25 tokenizer can match against
    jieba-segmented indexed content.
    """
    return segment_text(query)
