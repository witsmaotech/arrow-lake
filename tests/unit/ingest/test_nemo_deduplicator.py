"""Tests for NeMoDeduplicator — Story 8.5 MinHash LSH dedup.

Tests exact CPU fallback deduplication and class attributes.
GPU MinHash path is only tested structurally (no GPU in CI).
"""

from __future__ import annotations

from unittest.mock import patch

import pyarrow as pa

from arrow_lake.quality.nemo_curator import NeMoDeduplicator


def _force_cpu():
    """Context manager to force CPU exact-dedup path."""
    return patch.object(NeMoDeduplicator, "_try_gpu", return_value=False)


class TestNeMoDeduplicatorInit:
    """Test NeMoDeduplicator initialization."""

    def test_name(self) -> None:
        d = NeMoDeduplicator()
        assert d.name == "nemo_dedup"

    def test_using_gpu_defaults_false(self) -> None:
        d = NeMoDeduplicator()
        assert d.using_gpu is False

    def test_default_params(self) -> None:
        d = NeMoDeduplicator()
        assert d._ngram_size == 5
        assert d._num_hashes == 128
        assert d._threshold == 0.8
        assert d._text_column == "text_content"

    def test_custom_params(self) -> None:
        d = NeMoDeduplicator(ngram_size=3, num_hashes=64, threshold=0.5)
        assert d._ngram_size == 3
        assert d._num_hashes == 64
        assert d._threshold == 0.5


class TestNeMoDeduplicatorFilter:
    """Test NeMoDeduplicator.deduplicate() on the CPU exact path."""

    def test_empty_table(self) -> None:
        d = NeMoDeduplicator()
        table = pa.table({"text_content": []})
        unique, dup = d.deduplicate(table)
        assert unique.num_rows == 0
        assert dup.num_rows == 0

    def test_table_without_text_column(self) -> None:
        d = NeMoDeduplicator()
        table = pa.table({"other": [1, 2, 3]})
        unique, dup = d.deduplicate(table)
        assert unique.num_rows == 3
        assert dup.num_rows == 0

    def test_exact_dedup_removes_duplicates(self) -> None:
        d = NeMoDeduplicator()
        table = pa.table({"text_content": ["hello", "hello", "world"]})
        with _force_cpu():
            unique, dup = d.deduplicate(table)
        assert unique.num_rows == 2
        assert dup.num_rows == 1

    def test_exact_dedup_preserves_first_occurrence(self) -> None:
        d = NeMoDeduplicator()
        table = pa.table({"text_content": ["A", "B", "A"]})
        with _force_cpu():
            unique, _dup = d.deduplicate(table)
        assert unique.num_rows == 2
        texts = unique.column("text_content").to_pylist()
        assert texts == ["A", "B"]

    def test_all_unique(self) -> None:
        d = NeMoDeduplicator()
        table = pa.table({"text_content": ["alpha", "beta", "gamma", "delta"]})
        with _force_cpu():
            unique, dup = d.deduplicate(table)
        assert unique.num_rows == 4
        assert dup.num_rows == 0

    def test_none_values_preserved(self) -> None:
        d = NeMoDeduplicator()
        table = pa.table({"text_content": ["hello", None, "world"]})
        with _force_cpu():
            unique, _dup = d.deduplicate(table)
        assert unique.num_rows >= 2

    def test_total_preserved(self) -> None:
        d = NeMoDeduplicator()
        table = pa.table({"text_content": ["x", "x", "y", "z"]})
        with _force_cpu():
            unique, dup = d.deduplicate(table)
        assert unique.num_rows + dup.num_rows == table.num_rows
