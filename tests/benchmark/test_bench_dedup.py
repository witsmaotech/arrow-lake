"""Content deduplication benchmarks — Story 4.7."""

from __future__ import annotations

import os

import numpy as np
import pyarrow as pa
import pytest

from tests.benchmark.benchmark_report import BenchmarkReport


def _make_binary_table(n: int = 10_000, dup_rate: float = 0.2) -> pa.Table:
    """Create a table with binary content and controlled duplication.

    Args:
        n: Total number of rows.
        dup_rate: Fraction of rows that are duplicates (point back to earlier rows).
    """
    rng = np.random.RandomState(42)
    unique_count = int(n * (1 - dup_rate))

    # Generate unique binary blobs
    unique_blobs = [os.urandom(256) for _ in range(unique_count)]
    # Generate duplicate rows pointing to earlier unique blobs
    dup_indices = rng.randint(0, unique_count, size=n - unique_count)

    blobs: list[bytes] = unique_blobs + [unique_blobs[i] for i in dup_indices]

    return pa.table(
        {
            "id": [f"row_{i:06d}" for i in range(n)],
            "image_data": blobs,
        }
    )


@pytest.mark.benchmark
class TestDedupBenchmark:
    """Benchmark content deduplication performance."""

    def test_exact_dedup_10k(self) -> None:
        """Benchmark: SHA-256 exact dedup on 10K rows (20% duplicates)."""
        from arrow_lake.quality.dedup import ContentDeduplicator

        table = _make_binary_table(n=10_000, dup_rate=0.2)
        d = ContentDeduplicator(strategy="exact", action="remove")

        report = BenchmarkReport("dedup_exact_10k")
        elapsed = report.measure(
            "SHA-256 exact dedup (10K rows, 20% dup)",
            lambda: d.deduplicate(table),
            rows=table.num_rows,
            repeats=5,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_exact_dedup_50k(self) -> None:
        """Benchmark: SHA-256 exact dedup on 50K rows."""
        from arrow_lake.quality.dedup import ContentDeduplicator

        table = _make_binary_table(n=50_000, dup_rate=0.3)
        d = ContentDeduplicator(strategy="exact", action="remove")

        report = BenchmarkReport("dedup_exact_50k")
        elapsed = report.measure(
            "SHA-256 exact dedup (50K rows, 30% dup)",
            lambda: d.deduplicate(table),
            rows=table.num_rows,
            repeats=3,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_exact_dedup_flag_mode_10k(self) -> None:
        """Benchmark: SHA-256 exact dedup flag mode on 10K rows."""
        from arrow_lake.quality.dedup import ContentDeduplicator

        table = _make_binary_table(n=10_000, dup_rate=0.2)
        d = ContentDeduplicator(strategy="exact", action="flag")

        report = BenchmarkReport("dedup_exact_flag_10k")
        elapsed = report.measure(
            "SHA-256 exact dedup flag mode (10K rows, 20% dup)",
            lambda: d.deduplicate(table),
            rows=table.num_rows,
            repeats=5,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_incremental_dedup_across_batches(self) -> None:
        """Benchmark: incremental dedup across 10 batches of 1K rows each."""
        from arrow_lake.quality.dedup import ContentDeduplicator

        d = ContentDeduplicator(strategy="exact", action="remove")
        seen: dict[str, str] = {}

        def run_incremental() -> None:
            nonlocal seen
            seen = {}
            for batch_idx in range(10):
                table = _make_binary_table(n=1_000, dup_rate=0.15)
                _result, seen = d.deduplicate_incremental(table, existing_sha256=seen)

        report = BenchmarkReport("dedup_incremental_10x1k")
        elapsed = report.measure(
            "Incremental dedup (10 x 1K batches, 15% cross-batch dup)",
            run_incremental,
            rows=10_000,
            repeats=3,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_sha256_hash_throughput(self) -> None:
        """Benchmark: raw SHA-256 hashing throughput for 256-byte blobs."""
        from arrow_lake.quality.dedup import ContentDeduplicator

        d = ContentDeduplicator()
        blobs = [os.urandom(256) for _ in range(50_000)]

        report = BenchmarkReport("sha256_hash_50k")
        elapsed = report.measure(
            "SHA-256 hash computation (50K x 256 bytes)",
            lambda: [d._compute_sha256(b) for b in blobs],
            rows=50_000,
            repeats=5,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0
