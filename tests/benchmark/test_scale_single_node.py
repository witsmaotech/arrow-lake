"""Single-node scalability benchmarks — Story 7.12.

NFR-SCALE-01: Single node up to 10M rows
NFR-SCALE-03: Concurrent query up to 100 QPS
NFR-PERF-01: Vector search latency (10M rows, top_k=100) < 10ms
"""

from __future__ import annotations

import concurrent.futures

import numpy as np
import pyarrow as pa
import pytest

from tests.benchmark.benchmark_report import BenchmarkReport


def _make_vector_table(n: int, dim: int = 128) -> pa.Table:
    """Create a table with vector + metadata columns for scaling tests."""
    rng = np.random.RandomState(42)
    vectors = rng.randn(n, dim).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    vectors = vectors / norms
    return pa.table(
        {
            "id": [f"doc_{i:08d}" for i in range(n)],
            "text_embedding": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
            "modality": [
                "image" if i % 3 == 0 else "text" if i % 3 == 1 else "video" for i in range(n)
            ],
            "quality_score": [0.5 + (i % 10) * 0.05 for i in range(n)],
        }
    )


@pytest.mark.benchmark
class TestIngestionScale:
    """Benchmark dataset ingestion at various scales."""

    def test_ingest_100k_rows(self, lance_tmp_dir: str) -> None:
        """Benchmark: create Lance dataset with 100K rows."""
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=lance_tmp_dir)

        report = BenchmarkReport("ingest_100k")
        call_count = 0

        def _create_and_cleanup():
            nonlocal call_count
            call_count += 1
            ds_name = f"scale_100k_{call_count}"
            table = _make_vector_table(100_000)
            storage.create_dataset(ds_name, table)

        elapsed = report.measure(
            "create_dataset (100K rows)",
            _create_and_cleanup,
            rows=100_000,
            repeats=3,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    @pytest.mark.slow
    def test_ingest_1m_rows(self, lance_tmp_dir: str) -> None:
        """Benchmark: create Lance dataset with 1M rows (NFR-SCALE-01 partial)."""
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_vector_table(1_000_000)

        report = BenchmarkReport("ingest_1m")
        elapsed = report.measure(
            "create_dataset (1M rows)",
            lambda: storage.create_dataset("scale_1m", table),
            rows=1_000_000,
            repeats=1,
            warmup=0,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    @pytest.mark.slow
    def test_ingest_10m_rows(self, lance_tmp_dir: str) -> None:
        """Benchmark: create Lance dataset with 10M rows (NFR-SCALE-01)."""
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_vector_table(10_000_000)

        report = BenchmarkReport("ingest_10m")
        elapsed = report.measure(
            "create_dataset (10M rows)",
            lambda: storage.create_dataset("scale_10m", table),
            rows=10_000_000,
            repeats=1,
            warmup=0,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0


@pytest.mark.benchmark
class TestQueryLatency:
    """Benchmark vector search latency at scale."""

    def test_vector_search_latency_at_10k(self, lance_tmp_dir: str) -> None:
        """Benchmark: vector search on 10K rows."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.vector import VectorSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_vector_table(10_000)
        storage.create_dataset("latency_10k", table)

        bridge = VectorSearchBridge(storage)
        rng = np.random.RandomState(123)
        query = rng.randn(128).astype(np.float32)
        query = query / np.linalg.norm(query)

        report = BenchmarkReport("search_latency_10k")
        elapsed = report.measure(
            "single query (10K rows, top_k=10)",
            lambda: bridge.search("latency_10k", query.tolist(), top_k=10),
            rows=10_000,
            repeats=20,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    @pytest.mark.slow
    def test_vector_search_latency_at_1m(self, lance_tmp_dir: str) -> None:
        """Benchmark: vector search on 1M rows."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.vector import VectorSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_vector_table(1_000_000)
        storage.create_dataset("latency_1m", table)

        bridge = VectorSearchBridge(storage)
        rng = np.random.RandomState(123)
        query = rng.randn(128).astype(np.float32)
        query = query / np.linalg.norm(query)

        report = BenchmarkReport("search_latency_1m")
        elapsed = report.measure(
            "single query (1M rows, top_k=10)",
            lambda: bridge.search("latency_1m", query.tolist(), top_k=10),
            rows=1_000_000,
            repeats=10,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    @pytest.mark.slow
    def test_vector_search_latency_at_10m(self, lance_tmp_dir: str) -> None:
        """Benchmark: vector search on 10M rows (NFR-PERF-01: < 10ms)."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.vector import VectorSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_vector_table(10_000_000)
        storage.create_dataset("latency_10m", table)

        bridge = VectorSearchBridge(storage)
        rng = np.random.RandomState(123)
        query = rng.randn(128).astype(np.float32)
        query = query / np.linalg.norm(query)

        report = BenchmarkReport("search_latency_10m")
        elapsed = report.measure(
            "single query (10M rows, top_k=100)",
            lambda: bridge.search("latency_10m", query.tolist(), top_k=100),
            rows=10_000_000,
            repeats=5,
        )
        report.print_summary()
        print(report.to_json())

        latency_ms = elapsed * 1000
        print(f"  NFR-PERF-01 check: {latency_ms:.1f}ms (target: < 10ms)")
        assert elapsed > 0


@pytest.mark.benchmark
class TestConcurrentQuery:
    """Benchmark concurrent query throughput (NFR-SCALE-03: 100 QPS)."""

    def test_10_qps_sustained(self, lance_tmp_dir: str) -> None:
        """Benchmark: 10 queries per second on 10K rows."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.vector import VectorSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_vector_table(10_000)
        storage.create_dataset("qps_10", table)

        bridge = VectorSearchBridge(storage)
        rng = np.random.RandomState(99)
        queries = []
        for _ in range(100):
            q = rng.randn(128).astype(np.float32)
            q = q / np.linalg.norm(q)
            queries.append(q.tolist())

        report = BenchmarkReport("concurrent_10_qps")

        def run_concurrent() -> None:
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(bridge.search, "qps_10", q, top_k=10) for q in queries]
                for f in futures:
                    f.result()

        elapsed = report.measure(
            "100 queries (10 concurrent workers, 10K rows)",
            run_concurrent,
            rows=100,
            repeats=3,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    @pytest.mark.slow
    def test_100_qps_sustained(self, lance_tmp_dir: str) -> None:
        """Benchmark: 100 queries per second (NFR-SCALE-03)."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.vector import VectorSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_vector_table(10_000)
        storage.create_dataset("qps_100", table)

        bridge = VectorSearchBridge(storage)
        rng = np.random.RandomState(99)
        queries = []
        for _ in range(1000):
            q = rng.randn(128).astype(np.float32)
            q = q / np.linalg.norm(q)
            queries.append(q.tolist())

        report = BenchmarkReport("concurrent_100_qps")

        def run_concurrent() -> None:
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(bridge.search, "qps_100", q, top_k=10) for q in queries]
                for f in futures:
                    f.result()

        elapsed = report.measure(
            "1000 queries (20 concurrent workers, 10K rows)",
            run_concurrent,
            rows=1000,
            repeats=3,
            warmup=1,
        )
        qps = 1000 / elapsed if elapsed > 0 else 0
        report.print_summary()
        print(f"  NFR-SCALE-03 check: {qps:.0f} QPS (target: >= 100 QPS)")
        print(report.to_json())
        assert elapsed > 0


@pytest.mark.benchmark
class TestOLAPAtScale:
    """Benchmark OLAP queries at scale."""

    @pytest.mark.slow
    def test_olap_group_by_1m(self, lance_tmp_dir: str) -> None:
        """Benchmark: GROUP BY on 1M rows."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.olap import OlapSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        table = _make_vector_table(1_000_000)
        storage.create_dataset("olap_1m", table)

        bridge = OlapSearchBridge(storage)

        report = BenchmarkReport("olap_groupby_1m")
        elapsed = report.measure(
            "GROUP BY modality (1M rows)",
            lambda: bridge.query(
                "olap_1m",
                "SELECT modality, COUNT(*) as cnt, AVG(quality_score) FROM olap_1m GROUP BY modality",
            ),
            rows=1_000_000,
            repeats=5,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0
