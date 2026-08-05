"""Mixed-load concurrency benchmarks — QPS under concurrent contention.

Real workloads do not run one query type at a time. A console user runs an
OLAP aggregation while a RAG request fires a vector search and a keyword
search hits FTS — all on the same Lake, all on threads from the API's
ThreadPoolExecutor-based sync bridge (``api/utils.run_sync``). This benchmark
sweeps the worker count to find the throughput plateau, the same shape as the
batch-3 #17 concurrency gate (``test_bench_batch3_gates``) but across a *mixed*
vector + FTS + OLAP workload instead of vector-only.

What it answers: does QPS scale near-linearly with workers, or does it plateau
(a sign of GIL / DuckDB session-pool / Lance scan contention)? The plateau
point is the effective concurrency ceiling of the sync query layer on one node.

Run::

    .venv/bin/pytest tests/benchmark/test_bench_concurrency.py -m benchmark -s
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pyarrow as pa
import pytest

from tests.benchmark.benchmark_report import BenchmarkReport, Measurement

_DIM = 128
_FTS_TERMS = ["machine learning", "data processing", "document", "neural", "statistics"]
_OLAP_SQL = (
    'SELECT "IATA_CODE_Reporting_Airline" AS airline, COUNT(*) AS flights, '
    'ROUND(AVG("ArrDelay"), 1) AS avg_arr_delay '
    'FROM "ontime" GROUP BY airline ORDER BY avg_arr_delay DESC LIMIT 10'
)


def _make_vector_table(n: int, dim: int = _DIM, seed: int = 7) -> pa.Table:
    """L2-normalized vector + text table (vector + FTS shareable schema)."""
    rng = np.random.RandomState(seed)
    vectors = rng.randn(n, dim).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    vectors = vectors / norms
    return pa.table(
        {
            "id": [f"doc_{i:06d}" for i in range(n)],
            "text_content": [
                f"Document number {i} about machine learning and data processing"
                for i in range(n)
            ],
            "text_embedding": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
        }
    )


def _make_olap_table(n: int, seed: int = 11) -> pa.Table:
    """Small ontime-schema table for the OLAP third of the mixed load."""
    rng = np.random.RandomState(seed)
    carriers = ["AA", "UA", "DL", "WN", "OO", "EV", "B6", "AS"]
    arr_delay = rng.normal(5.0, 25.0, n).astype(np.float64)
    return pa.table(
        {
            "IATA_CODE_Reporting_Airline": rng.choice(carriers, n),
            "ArrDelay": arr_delay,
        }
    )


@pytest.mark.benchmark
class TestConcurrencyBenchmark:
    """Benchmark mixed vector + FTS + OLAP throughput under N workers."""

    def test_mixed_load_qps_sweep(self, lance_tmp_dir: str) -> None:
        """Sweep workers [1, 5, 10, 20] over a 300-op mixed workload.

        Each op is one query; ``i % 3`` selects vector / FTS / OLAP so the
        load is evenly mixed (100 ops of each per run). QPS = ops / wall time.
        """
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.fts import FullTextSearchBridge
        from arrow_lake.query.olap import OlapSearchBridge
        from arrow_lake.query.vector import VectorSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        n = 10_000

        # --- build the three datasets + indexes once ---
        vec_table = _make_vector_table(n)
        storage.create_dataset("conc_vec", vec_table)
        vbridge = VectorSearchBridge(storage)
        vbridge.create_index("conc_vec", vector_column="text_embedding", num_sub_vectors=16)

        # FTS reuses the same text column on a separate dataset to keep indexes isolated.
        fts_table = pa.table(
            {
                "id": vec_table.column("id"),
                "text_content": vec_table.column("text_content"),
            }
        )
        storage.create_dataset("conc_fts", fts_table)
        fbridge = FullTextSearchBridge(storage)
        fbridge.create_index("conc_fts")

        storage.create_dataset("ontime", _make_olap_table(n))
        olap_bridge = OlapSearchBridge(storage)

        # --- pre-build the query pools (no allocation inside the timed region) ---
        rng = np.random.RandomState(99)
        vec_queries = [
            (rng.randn(_DIM).astype(np.float32) / np.linalg.norm(rng.randn(_DIM))).tolist()
            for _ in range(50)
        ]

        def _mixed_query(i: int) -> None:
            kind = i % 3
            if kind == 0:
                vbridge.search(
                    "conc_vec", vec_queries[i % len(vec_queries)],
                    top_k=10, vector_column="text_embedding",
                )
            elif kind == 1:
                fbridge.search("conc_fts", _FTS_TERMS[i % len(_FTS_TERMS)], top_k=10)
            else:
                olap_bridge.query("ontime", _OLAP_SQL)

        total_ops = 300
        _mixed_query(0)  # warmup

        report = BenchmarkReport("concurrency_mixed_load_qps")
        print(f"\n[concurrency] mixed vector+FTS+OLAP, {total_ops} ops ({total_ops // 3} each)")
        for workers in (1, 5, 10, 20):
            with ThreadPoolExecutor(max_workers=workers) as ex:
                t0 = time.perf_counter()
                list(ex.map(_mixed_query, range(total_ops)))
                elapsed = time.perf_counter() - t0
            qps = total_ops / elapsed if elapsed > 0 else 0.0
            report.add(
                Measurement(
                    label=f"workers={workers}",
                    elapsed_seconds=elapsed,
                    throughput=qps,
                    rows=total_ops,
                )
            )
            print(f"  workers={workers:2d}  →  {qps:7.1f} QPS  ({elapsed:.2f}s)")

        report.print_summary()
        print(report.to_json())
        assert qps > 0
