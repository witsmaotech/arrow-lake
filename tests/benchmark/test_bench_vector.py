"""Vector search benchmarks — Story 5.8."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from tests.benchmark.benchmark_report import BenchmarkReport


def _make_vector_table(n: int, dim: int = 128) -> pa.Table:
    """Create a table with text_embedding vector column."""
    rng = np.random.RandomState(42)
    vectors = rng.randn(n, dim).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    vectors = vectors / norms
    return pa.table(
        {
            "id": [f"doc_{i:06d}" for i in range(n)],
            "text_content": [
                f"Document number {i} about machine learning and data processing" for i in range(n)
            ],
            "text_embedding": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
        }
    )


@pytest.mark.benchmark
class TestVectorSearchBenchmark:
    """Benchmark vector search latency and throughput."""

    def test_vector_search_10k_bruteforce(self, lance_tmp_dir: str) -> None:
        """Benchmark: brute-force vector search on 10K rows."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.vector import VectorSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_vector_table(10_000)
        storage.create_dataset("bench_10k", table)

        bridge = VectorSearchBridge(storage)
        rng = np.random.RandomState(123)
        query = rng.randn(128).astype(np.float32)
        query = query / np.linalg.norm(query)

        report = BenchmarkReport("vector_search_10k_bruteforce")
        elapsed = report.measure(
            "single query (10K rows, no index)",
            lambda: bridge.search("bench_10k", query.tolist(), top_k=10),
            rows=10_000,
            repeats=10,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_vector_search_100k_with_index(self, lance_tmp_dir: str) -> None:
        """Benchmark: vector search on 100K rows with IVF_PQ index."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.vector import VectorSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_vector_table(100_000)
        storage.create_dataset("bench_100k", table)

        bridge = VectorSearchBridge(storage)

        report_idx = BenchmarkReport("vector_index_creation_100k")
        report_idx.measure(
            "IVF_PQ index (100K rows)",
            lambda: bridge.create_index(
                "bench_100k",
                vector_column="text_embedding",
                num_sub_vectors=16,  # 128 / 16 = 8
            ),
            rows=100_000,
            repeats=1,
            warmup=0,
        )
        report_idx.print_summary()

        rng = np.random.RandomState(42)
        query = rng.randn(128).astype(np.float32)
        query = query / np.linalg.norm(query)

        report = BenchmarkReport("vector_search_100k_indexed")
        elapsed = report.measure(
            "single query (100K rows, IVF_PQ)",
            lambda: bridge.search("bench_100k", query.tolist(), top_k=10),
            rows=100_000,
            repeats=10,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_vector_search_throughput(self, lance_tmp_dir: str) -> None:
        """Benchmark: vector search queries per second."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.vector import VectorSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_vector_table(10_000)
        storage.create_dataset("bench_qps", table)

        bridge = VectorSearchBridge(storage)
        rng = np.random.RandomState(99)
        queries = []
        for _ in range(100):
            q = rng.randn(128).astype(np.float32)
            q = q / np.linalg.norm(q)
            queries.append(q.tolist())

        report = BenchmarkReport("vector_search_throughput")

        def batch_search() -> None:
            for q in queries:
                bridge.search("bench_qps", q, top_k=10)

        elapsed = report.measure(
            "100 queries (10K rows)",
            batch_search,
            rows=100,
            repeats=3,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0
