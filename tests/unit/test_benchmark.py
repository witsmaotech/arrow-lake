"""Performance benchmark suite — Story 5.8.

Provides lightweight timing benchmarks for key search and query paths.
Results are printed to stdout (not asserted) — they serve as a
performance baseline, not a pass/fail gate.
"""

from __future__ import annotations

import time

import pyarrow as pa
from arrow_lake.quality.base import QualityFilterRegistry
from arrow_lake.quality.builtin import TextLengthFilter
from arrow_lake.quality.models import QualityReport


def _make_table(n: int, dim: int = 384) -> pa.Table:
    """Create a test table with text and embedding columns."""
    return pa.table(
        {
            "text_content": [f"document text number {i} for quality testing" for i in range(n)],
            "text_embedding": [list(range(dim)) for _ in range(n)],
            "id": list(range(n)),
        }
    )


class TestQualityBenchmark:
    """Benchmark quality filtering at scale."""

    def test_quality_filter_10k_rows(self) -> None:
        """Benchmark: TextLengthFilter on 10K rows."""
        registry = QualityFilterRegistry()
        registry.register(TextLengthFilter(min_chars=3))

        table = _make_table(10_000)
        start = time.perf_counter()
        report = registry.apply_all(table, active_filters="text_length")
        elapsed = time.perf_counter() - start

        assert report.total == 10_000
        print(
            f"\n[BENCHMARK] QualityFilter 10K rows: {elapsed:.4f}s "
            f"({report.total / elapsed:,.0f} rows/s)"
        )

    def test_quality_filter_100k_rows(self) -> None:
        """Benchmark: TextLengthFilter on 100K rows."""
        registry = QualityFilterRegistry()
        registry.register(TextLengthFilter(min_chars=3))

        table = _make_table(100_000)
        start = time.perf_counter()
        report = registry.apply_all(table, active_filters="text_length")
        elapsed = time.perf_counter() - start

        assert report.total == 100_000
        print(
            f"\n[BENCHMARK] QualityFilter 100K rows: {elapsed:.4f}s "
            f"({report.total / elapsed:,.0f} rows/s)"
        )


class TestQualityReportBenchmark:
    """Benchmark quality report serialization."""

    def test_to_json_1k_filters(self) -> None:
        """Benchmark: to_json with 1K filter results."""
        from arrow_lake.quality.models import FilterResult

        filters = tuple(
            FilterResult(filter_name=f"filter_{i}", passed_count=900 + i, rejected_count=100 - i)
            for i in range(1000)
        )
        report = QualityReport(
            total=100_000,
            passed=99_000,
            rejected=1000,
            filter_results=filters,
        )
        start = time.perf_counter()
        _ = report.to_json()
        elapsed = time.perf_counter() - start

        print(f"\n[BENCHMARK] to_json 1K filters: {elapsed:.4f}s")

    def test_per_filter_breakdown_100_filters(self) -> None:
        """Benchmark: per_filter_breakdown with 100 filters."""
        from arrow_lake.quality.models import FilterResult

        filters = tuple(
            FilterResult(filter_name=f"f_{i}", passed_count=990, rejected_count=10)
            for i in range(100)
        )
        report = QualityReport(total=10_000, filter_results=filters)
        start = time.perf_counter()
        _ = report.per_filter_breakdown()
        elapsed = time.perf_counter() - start

        print(f"\n[BENCHMARK] per_filter_breakdown 100 filters: {elapsed:.4f}s")


class TestStreamingBenchmark:
    """Benchmark streaming result iteration."""

    def test_streaming_100k_batch_1k(self) -> None:
        """Benchmark: StreamingResult iteration over 100K rows."""
        from arrow_lake.query.streaming import StreamingResult

        table = _make_table(100_000)
        start = time.perf_counter()
        sr = StreamingResult(table, batch_size=1000)
        total_yielded = 0
        for batch in sr:
            total_yielded += batch.num_rows
        elapsed = time.perf_counter() - start

        assert total_yielded == 100_000
        print(
            f"\n[BENCHMARK] Streaming 100K rows batch=1K: {elapsed:.4f}s "
            f"({total_yielded / elapsed:,.0f} rows/s)"
        )

    def test_streaming_100k_batch_10k(self) -> None:
        """Benchmark: StreamingResult iteration over 100K rows with larger batches."""
        from arrow_lake.query.streaming import StreamingResult

        table = _make_table(100_000)
        start = time.perf_counter()
        sr = StreamingResult(table, batch_size=10_000)
        total_yielded = 0
        for batch in sr:
            total_yielded += batch.num_rows
        elapsed = time.perf_counter() - start

        assert total_yielded == 100_000
        print(
            f"\n[BENCHMARK] Streaming 100K rows batch=10K: {elapsed:.4f}s "
            f"({total_yielded / elapsed:,.0f} rows/s)"
        )
