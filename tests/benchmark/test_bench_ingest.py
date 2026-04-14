"""Dataset ingestion benchmarks — Story 5.8."""

from __future__ import annotations

import pytest

from tests.benchmark.benchmark_report import BenchmarkReport


@pytest.mark.benchmark
class TestIngestBenchmark:
    """Benchmark dataset creation and open operations."""

    def test_ingest_10k_rows(self, lance_tmp_dir: str) -> None:
        """Benchmark: create dataset with 10K rows."""
        import numpy as np
        import pyarrow as pa
        from arrow_lake.ingest.storage import LanceStorageManager

        n, dim = 10_000, 128
        rng = np.random.RandomState(42)
        vectors = rng.randn(n, dim).astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        vectors = vectors / norms
        table = pa.table(
            {
                "id": [f"doc_{i:06d}" for i in range(n)],
                "text_content": [f"Document {i}" for i in range(n)],
                "vector": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
            }
        )

        storage = LanceStorageManager(base_uri=lance_tmp_dir)

        def create_and_delete() -> None:
            if storage.dataset_exists("bench_ingest_10k"):
                storage.delete_dataset("bench_ingest_10k")
            storage.create_dataset("bench_ingest_10k", table)

        report = BenchmarkReport("ingest_10k")
        elapsed = report.measure(
            "create_dataset (10K rows, 128d vectors)",
            create_and_delete,
            rows=n,
            repeats=5,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_dataset_open(self, lance_tmp_dir: str) -> None:
        """Benchmark: dataset open latency."""
        import pyarrow as pa
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        n = 10_000
        table = pa.table(
            {
                "id": [f"doc_{i:06d}" for i in range(n)],
                "text_content": [f"doc {i}" for i in range(n)],
            }
        )
        storage.create_dataset("bench_open", table)

        report = BenchmarkReport("dataset_open_10k")
        elapsed = report.measure(
            "open_dataset (10K rows)",
            lambda: storage.open_dataset("bench_open"),
            rows=n,
            repeats=20,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_dataset_read(self, lance_tmp_dir: str) -> None:
        """Benchmark: full dataset read latency."""
        import pyarrow as pa
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        n = 10_000
        table = pa.table(
            {
                "id": [f"doc_{i:06d}" for i in range(n)],
                "text_content": [f"doc {i}" for i in range(n)],
            }
        )
        storage.create_dataset("bench_read", table)

        report = BenchmarkReport("dataset_read_10k")
        elapsed = report.measure(
            "read_dataset (10K rows)",
            lambda: storage.read_dataset("bench_read"),
            rows=n,
            repeats=20,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0
