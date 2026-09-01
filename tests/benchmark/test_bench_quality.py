"""Quality filtering and streaming benchmarks — Story 5.8.

Migrated from tests/unit/test_benchmark.py with BenchmarkReport wrapping.
"""



from __future__ import annotations

import pytest

pytestmark = pytest.mark.benchmark

import pytest

from tests.benchmark.benchmark_report import BenchmarkReport


def _make_table(n: int, dim: int = 384) -> object:
    """Create a test table with text and embedding columns."""
    import pyarrow as pa

    return pa.table(
        {
            "text_content": [f"document text number {i} for quality testing" for i in range(n)],
            "text_embedding": [list(range(dim)) for _ in range(n)],
            "id": list(range(n)),
        }
    )


@pytest.mark.benchmark
class TestQualityBenchmark:
    """Benchmark quality filtering at scale."""

    def test_quality_filter_10k_rows(self) -> None:
        """Benchmark: TextLengthFilter on 10K rows."""
        from arrow_lake.quality.base import QualityFilterRegistry
        from arrow_lake.quality.builtin import TextLengthFilter

        registry = QualityFilterRegistry()
        registry.register(TextLengthFilter(min_chars=3))

        table = _make_table(10_000)

        report = BenchmarkReport("quality_filter_10k")
        elapsed = report.measure(
            "TextLengthFilter (10K rows)",
            lambda: registry.apply_all(table, active_filters="text_length"),
            rows=10_000,
            repeats=10,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_quality_filter_100k_rows(self) -> None:
        """Benchmark: TextLengthFilter on 100K rows."""
        from arrow_lake.quality.base import QualityFilterRegistry
        from arrow_lake.quality.builtin import TextLengthFilter

        registry = QualityFilterRegistry()
        registry.register(TextLengthFilter(min_chars=3))

        table = _make_table(100_000)

        report = BenchmarkReport("quality_filter_100k")
        elapsed = report.measure(
            "TextLengthFilter (100K rows)",
            lambda: registry.apply_all(table, active_filters="text_length"),
            rows=100_000,
            repeats=5,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0


@pytest.mark.benchmark
class TestQualityReportBenchmark:
    """Benchmark quality report serialization."""

    def test_to_json_1k_filters(self) -> None:
        """Benchmark: to_json with 1K filter results."""
        from arrow_lake.quality.models import FilterResult, QualityReport

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

        report_bench = BenchmarkReport("to_json_1k_filters")
        elapsed = report_bench.measure(
            "to_json (1K filters)",
            lambda: report.to_json(),
            repeats=20,
        )
        report_bench.print_summary()
        print(report_bench.to_json())
        assert elapsed > 0


@pytest.mark.benchmark
class TestStreamingBenchmark:
    """Benchmark streaming result iteration."""

    def test_streaming_100k_batch_1k(self) -> None:
        """Benchmark: StreamingResult iteration over 100K rows."""
        from arrow_lake.query.streaming import StreamingResult

        table = _make_table(100_000)

        report = BenchmarkReport("streaming_100k_batch_1k")
        elapsed = report.measure(
            "streaming (100K rows, batch=1K)",
            lambda: sum(b.num_rows for b in StreamingResult(table, batch_size=1000)),
            rows=100_000,
            repeats=5,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0
