"""Hybrid search benchmarks — Story 5.8."""



from __future__ import annotations

import pytest

pytestmark = pytest.mark.benchmark

import numpy as np
import pyarrow as pa
import pytest

from tests.benchmark.benchmark_report import BenchmarkReport


def _make_hybrid_table(n: int, dim: int = 128) -> pa.Table:
    """Create a table with text + vector columns for hybrid search."""
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
class TestHybridBenchmark:
    """Benchmark hybrid vector + FTS search."""

    def test_hybrid_search_10k(self, lance_tmp_dir: str) -> None:
        """Benchmark: hybrid search on 10K rows."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.fts import FullTextSearchBridge
        from arrow_lake.query.hybrid import HybridSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_hybrid_table(10_000)
        storage.create_dataset("bench_hybrid", table)

        fts_bridge = FullTextSearchBridge(storage)
        fts_bridge.create_index("bench_hybrid")

        hybrid_bridge = HybridSearchBridge(storage)

        rng = np.random.RandomState(42)
        query = rng.randn(128).astype(np.float32)
        query = query / np.linalg.norm(query)

        report = BenchmarkReport("hybrid_search_10k")
        elapsed = report.measure(
            "single hybrid query (10K rows)",
            lambda: hybrid_bridge.search(
                "bench_hybrid",
                query.tolist(),
                "machine learning",
                top_k=10,
                vector_column="text_embedding",
            ),
            rows=10_000,
            repeats=10,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_rrf_fusion_1k(self) -> None:
        """Benchmark: RRF fusion overhead on 1K results."""
        from arrow_lake.query.hybrid import HybridSearchBridge

        n = 1000
        rng = np.random.RandomState(42)
        vector_table = pa.table(
            {
                "id": [f"doc_{i:06d}" for i in range(n)],
                "text_content": [f"text {i}" for i in range(n)],
                "_distance": pa.array(rng.random(n).astype(np.float32)),
            }
        )
        fts_table = pa.table(
            {
                "id": [f"doc_{i:06d}" for i in range(n)],
                "text_content": [f"text {i}" for i in range(n)],
                "_score": pa.array(rng.random(n).astype(np.float32)),
            }
        )

        report = BenchmarkReport("rrf_fusion_1k")
        elapsed = report.measure(
            "RRF fusion (1K results)",
            lambda: HybridSearchBridge._rrf_fuse(vector_table, fts_table, k=60, top_k=10),
            rows=n,
            repeats=100,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0
