"""Tests for Story 5.5 — Streaming Results."""

from __future__ import annotations

import pyarrow as pa
import pytest
from arrow_lake.query.streaming import StreamingResult


def _make_table(n: int) -> pa.Table:
    return pa.table({"id": list(range(n)), "text": [f"row-{i}" for i in range(n)]})


class TestStreamingResult:
    """Test StreamingResult iterator."""

    def test_iterate_exact_batches(self) -> None:
        table = _make_table(10)
        sr = StreamingResult(table, batch_size=3)
        batches = list(sr)
        assert len(batches) == 4  # 3+3+3+1
        assert batches[0].num_rows == 3
        assert batches[1].num_rows == 3
        assert batches[2].num_rows == 3
        assert batches[3].num_rows == 1

    def test_iterate_single_row_batches(self) -> None:
        table = _make_table(5)
        sr = StreamingResult(table, batch_size=1)
        batches = list(sr)
        assert len(batches) == 5
        for b in batches:
            assert b.num_rows == 1

    def test_iterate_batch_larger_than_table(self) -> None:
        table = _make_table(3)
        sr = StreamingResult(table, batch_size=100)
        batches = list(sr)
        assert len(batches) == 1
        assert batches[0].num_rows == 3

    def test_empty_table(self) -> None:
        table = _make_table(0)
        sr = StreamingResult(table)
        batches = list(sr)
        assert len(batches) == 0

    def test_total_rows(self) -> None:
        table = _make_table(42)
        sr = StreamingResult(table)
        assert sr.total_rows == 42

    def test_remaining_rows(self) -> None:
        table = _make_table(10)
        sr = StreamingResult(table, batch_size=3)
        assert sr.remaining_rows == 10
        _ = next(sr)
        assert sr.remaining_rows == 7
        _ = next(sr)
        assert sr.remaining_rows == 4
        _ = next(sr)
        assert sr.remaining_rows == 1
        _ = next(sr)
        assert sr.remaining_rows == 0

    def test_is_exhausted(self) -> None:
        table = _make_table(5)
        sr = StreamingResult(table, batch_size=5)
        assert not sr.is_exhausted
        _ = next(sr)
        assert sr.is_exhausted

    def test_stop_iteration(self) -> None:
        table = _make_table(2)
        sr = StreamingResult(table, batch_size=2)
        _ = next(sr)  # first batch
        with pytest.raises(StopIteration):
            next(sr)

    def test_columns(self) -> None:
        table = _make_table(5)
        sr = StreamingResult(table)
        assert sr.columns == ["id", "text"]

    def test_schema(self) -> None:
        table = _make_table(5)
        sr = StreamingResult(table)
        assert sr.schema == table.schema

    def test_collect_returns_remaining(self) -> None:
        table = _make_table(10)
        sr = StreamingResult(table, batch_size=3)
        _ = next(sr)  # consume 3
        remaining = sr.collect()
        assert remaining.num_rows == 7
        assert sr.is_exhausted

    def test_collect_from_start(self) -> None:
        table = _make_table(5)
        sr = StreamingResult(table)
        collected = sr.collect()
        assert collected.num_rows == 5

    def test_collect_empty(self) -> None:
        table = _make_table(0)
        sr = StreamingResult(table)
        collected = sr.collect()
        assert collected.num_rows == 0

    def test_batch_size_minimum_one(self) -> None:
        table = _make_table(5)
        sr = StreamingResult(table, batch_size=0)
        assert sr._batch_size == 1  # clamped to 1
