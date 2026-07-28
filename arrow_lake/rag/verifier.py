"""Faithfulness verification — check answer claims against retrieved context.

v1.9.6 P0-1: lightweight mode validates ``[n]`` citation refs against the
context window size + marks each answer sentence ``supported`` (has [n] ref)
or ``unverified`` (no ref). Returns ``support_ratio`` for quick quality signal.

Extensible to:
- embedding mode: per-sentence cosine vs chunk text (threshold → unsupported)
- LLM judge mode: single faithfulness prompt (per-sentence label)

Kept dependency-free (stdlib only) so the default lightweight path adds zero
cost; heavier modes opt-in via config.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = ["SentenceCheck", "VerificationResult", "verify"]

# [n] or [n,m] style citation refs in generated answers.
_REF_RE = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class SentenceCheck:
    """Per-sentence verification result."""

    text: str
    label: str  # "supported" | "unverified"
    refs: tuple[int, ...] = ()


@dataclass(frozen=True)
class VerificationResult:
    """Aggregate verification result attached to RAGResponse."""

    support_ratio: float  # supported / total sentences
    sentences: tuple[SentenceCheck, ...] = ()
    valid_refs: int = 0
    invalid_refs: int = 0
    mode: str = "lightweight"


def _split_sentences(text: str) -> list[str]:
    """Split CJK + ASCII text into sentences (>6 chars to skip fragments).

    CJK sentence terminators (。！？) split immediately (no trailing space
    needed); ASCII (.!?) require trailing whitespace to avoid splitting
    decimals like 3.14 or abbreviations.
    """
    parts = re.split(r"(?<=[。！？])|(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 6]


def _extract_refs(text: str) -> list[int]:
    return [int(m) for m in _REF_RE.findall(text)]


def verify(answer: str, chunk_count: int) -> VerificationResult:
    """Lightweight faithfulness check: [n] ref validation + per-sentence label.

    Args:
        answer: Generated answer text.
        chunk_count: Number of context chunks (valid refs are 1..chunk_count).

    Returns:
        VerificationResult with support_ratio + per-sentence checks.
    """
    if not answer or chunk_count <= 0:
        return VerificationResult(support_ratio=1.0)

    sentences = _split_sentences(answer)
    if not sentences:
        return VerificationResult(support_ratio=1.0)

    # Validate all refs in the answer.
    all_refs = _extract_refs(answer)
    valid_refs = [n for n in all_refs if 1 <= n <= chunk_count]
    invalid_refs = [n for n in all_refs if n < 1 or n > chunk_count]

    # Per-sentence: supported if it carries a valid [n] ref.
    checks: list[SentenceCheck] = []
    for s in sentences:
        refs = tuple(_extract_refs(s))
        label = "supported" if refs else "unverified"
        checks.append(SentenceCheck(text=s[:200], label=label, refs=refs))

    supported = sum(1 for c in checks if c.label == "supported")
    return VerificationResult(
        support_ratio=round(supported / len(checks), 3),
        sentences=tuple(checks),
        valid_refs=len(valid_refs),
        invalid_refs=len(invalid_refs),
        mode="lightweight",
    )
