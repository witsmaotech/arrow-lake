"""Full-text search benchmarks — Story 5.8."""



from __future__ import annotations

import pytest

pytestmark = pytest.mark.benchmark

import numpy as np
import pyarrow as pa
import pytest

from tests.benchmark.benchmark_report import BenchmarkReport


def _make_fts_table(n: int) -> pa.Table:
    """Create a table with text + dummy vector column for LanceDB compatibility."""
    dim = 8  # small dummy vector for LanceDB search() API
    return pa.table(
        {
            "id": [f"doc_{i:06d}" for i in range(n)],
            "text_content": [
                f"Document number {i} about machine learning and data science" for i in range(n)
            ],
            "text_embedding": pa.FixedSizeListArray.from_arrays(
                np.zeros(n * dim, dtype=np.float32), dim
            ),
        }
    )


@pytest.mark.benchmark
class TestFTSBenchmark:
    """Benchmark full-text search latency and throughput."""

    def test_fts_search_10k(self, lance_tmp_dir: str) -> None:
        """Benchmark: FTS search on 10K rows with index."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.fts import FullTextSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_fts_table(10_000)
        storage.create_dataset("bench_fts_10k", table)

        bridge = FullTextSearchBridge(storage)
        bridge.create_index("bench_fts_10k")

        report = BenchmarkReport("fts_search_10k")
        elapsed = report.measure(
            "single FTS query (10K rows)",
            lambda: bridge.search("bench_fts_10k", "machine learning", top_k=10),
            rows=10_000,
            repeats=10,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_fts_index_creation(self, lance_tmp_dir: str) -> None:
        """Benchmark: FTS index creation time."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.fts import FullTextSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_fts_table(50_000)
        storage.create_dataset("bench_fts_create", table)

        bridge = FullTextSearchBridge(storage)
        report = BenchmarkReport("fts_index_creation_50k")
        elapsed = report.measure(
            "FTS index (50K rows)",
            lambda: bridge.create_index("bench_fts_create"),
            rows=50_000,
            repeats=1,
            warmup=0,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_fts_throughput(self, lance_tmp_dir: str) -> None:
        """Benchmark: FTS queries per second."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.fts import FullTextSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_fts_table(10_000)
        storage.create_dataset("bench_fts_qps", table)

        bridge = FullTextSearchBridge(storage)
        bridge.create_index("bench_fts_qps")

        queries = [
            "machine learning",
            "data processing",
            "neural networks",
            "statistics",
            "algorithms",
        ] * 10

        report = BenchmarkReport("fts_throughput")

        def batch_search() -> None:
            for q in queries:
                bridge.search("bench_fts_qps", q, top_k=10)

        elapsed = report.measure(
            "50 queries (10K rows)",
            batch_search,
            rows=50,
            repeats=3,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0
