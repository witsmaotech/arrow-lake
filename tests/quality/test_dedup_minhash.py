"""Tests for MinHash LSH semantic text deduplication (data-prep · WS1).

Covers the new ``strategy="minhash"`` branch of ``ContentDeduplicator`` —
real CPU MinHash+LSH via datasketch, not gated on GPU/NeMo.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from arrow_lake.quality.dedup import ContentDeduplicator


def _table() -> pa.Table:
    """Four rows: row 1 is a near-duplicate of row 0 (one-char edit → high Jaccard)."""
    return pa.table(
        {
            "id": [1, 2, 3, 4],
            "text_content": [
                "注意力机制是深度学习的核心技术，它让模型能够关注重要信息。",
                "注意力机制是深度学习的核心技术，它让模型能够关注关键信息。",
                "长江白鳍豚保护区位于安徽省芜湖市无为段长江江心洲。",
                "Transformer 架构由 Google 在 2017 年提出，深刻改变了自然语言处理领域。",
            ],
        }
    )


def test_minhash_requires_text_column() -> None:
    with pytest.raises(ValueError):
        ContentDeduplicator(strategy="minhash")


def test_minhash_rejects_bad_strategy() -> None:
    with pytest.raises(ValueError):
        ContentDeduplicator(strategy="fuzzy", text_column="text_content")


def test_minhash_flags_near_duplicate() -> None:
    dedup = ContentDeduplicator(
        strategy="minhash", action="flag", text_column="text_content", threshold=0.5
    )
    result = dedup.deduplicate(_table())
    assert result.strategy == "minhash"
    assert result.duplicates_found >= 1
    flags = result.table.column("is_duplicate").to_pylist()
    assert flags[0] is False  # first occurrence kept
    assert flags[1] is True  # near-dup of row 0


def test_minhash_remove_keeps_unique_only() -> None:
    dedup = ContentDeduplicator(
        strategy="minhash", action="remove", text_column="text_content", threshold=0.5
    )
    result = dedup.deduplicate(_table())
    assert result.unique_rows < result.total_rows
    assert result.table.num_rows == result.unique_rows
    assert "is_duplicate" not in result.table.column_names


def test_minhash_distinct_rows_not_flagged() -> None:
    """All-distinct texts → no duplicates."""
    t = pa.table(
        {
            "id": [1, 2, 3],
            "text_content": [
                "量子计算利用叠加态与纠缠实现并行。",
                "光合作用把太阳能转化为化学能。",
                "罗马帝国分裂为东西两部分。",
            ],
        }
    )
    dedup = ContentDeduplicator(
        strategy="minhash", action="flag", text_column="text_content", threshold=0.5
    )
    result = dedup.deduplicate(t)
    assert result.duplicates_found == 0
    assert result.unique_rows == 3


def test_minhash_empty_table() -> None:
    dedup = ContentDeduplicator(strategy="minhash", text_column="text_content")
    empty = pa.table({"id": pa.array([], type=pa.int64()), "text_content": pa.array([], type=pa.string())})
    result = dedup.deduplicate(empty)
    assert result.total_rows == 0
    assert result.duplicates_found == 0


def test_minhash_missing_text_column_in_table_keeps_all() -> None:
    """If the text column is absent from the table, nothing is flagged (graceful)."""
    dedup = ContentDeduplicator(strategy="minhash", text_column="text_content")
    t = pa.table({"id": [1, 2], "body": ["a", "b"]})
    result = dedup.deduplicate(t)
    assert result.duplicates_found == 0
    assert result.unique_rows == 2
