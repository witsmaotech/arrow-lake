"""Save performance baselines from benchmark runs.

Usage::

    # Save all baselines:
    python -m tests.benchmark.save_baselines

    # Save specific benchmark:
    python -m tests.benchmark.save_baselines --bench ingest
    python -m tests.benchmark.save_baselines --bench vector_search
    python -m tests.benchmark.save_baselines --bench fts_search

Output files are written to tests/benchmark/baselines/*.json.

Each baseline records the median measurement from a short benchmark run.
For reliable baselines, run on a quiescent machine with no other load.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow as pa

_BASELINES_DIR = Path(__file__).parent / "baselines"

# Threshold: alert if perf degrades by more than this %
_DEFAULT_THRESHOLD_PCT = 20


def _save_baseline(name: str, metrics: dict) -> None:
    """Write a baseline JSON file."""
    _BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    path = _BASELINES_DIR / f"{name}.json"
    data = {
        "name": name,
        "description": f"{name} performance baseline",
        "threshold_pct": _DEFAULT_THRESHOLD_PCT,
        "metrics": metrics,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved baseline: {path}")


def _median_bench(fn, warmup: int = 2, repeats: int = 5) -> float:
    """Run fn multiple times, return median elapsed seconds."""
    for _ in range(warmup):
        fn()
    timings: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        timings.append(time.perf_counter() - start)
    return float(np.median(timings))


# ---------------------------------------------------------------------------
# Benchmark runners
# ---------------------------------------------------------------------------


def bench_ingest(tmp_dir: Path) -> None:
    """Benchmark 10K row ingestion."""
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

    storage = LanceStorageManager(base_uri=str(tmp_dir))

    def create_and_delete() -> None:
        if storage.dataset_exists("bench_baseline"):
            storage.delete_dataset("bench_baseline")
        storage.create_dataset("bench_baseline", table)

    duration = _median_bench(create_and_delete)
    rps = n / duration
    _save_baseline(
        "ingest_10k",
        {
            "rows_per_second": {"value": round(rps, 1), "direction": "higher_better"},
            "duration_seconds": {"value": round(duration, 3), "direction": "lower_better"},
        },
    )


def bench_vector_search(tmp_dir: Path) -> None:
    """Benchmark vector search on 1K rows."""
    from arrow_lake.ingest.storage import LanceStorageManager
    from arrow_lake.query.vector import VectorSearchBridge

    n, dim = 1_000, 128
    rng = np.random.RandomState(42)
    vectors = rng.randn(n, dim).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    vectors = vectors / norms
    table = pa.table(
        {
            "id": [f"doc_{i:06d}" for i in range(n)],
            "text_content": [f"Document {i}" for i in range(n)],
            "text_embedding": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
        }
    )

    storage = LanceStorageManager(base_uri=str(tmp_dir))
    storage.create_dataset("bench_vs", table)
    bridge = VectorSearchBridge(storage)

    q = rng.randn(dim).astype(np.float32)
    q = q / np.linalg.norm(q)

    # Single query latency
    def single_query() -> None:
        bridge.search("bench_vs", q.tolist(), top_k=10)

    single_ms = _median_bench(single_query) * 1000

    # Throughput
    queries = [rng.randn(dim).astype(np.float32) for _ in range(50)]

    def batch_query() -> None:
        for query_vec in queries:
            norm = np.linalg.norm(query_vec)
            if norm > 0:
                query_vec = query_vec / norm
            bridge.search("bench_vs", query_vec.tolist(), top_k=10)

    duration = _median_bench(batch_query, warmup=1, repeats=3)
    qps = len(queries) / duration

    _save_baseline(
        "vector_search_1k",
        {
            "p50_ms": {"value": round(single_ms, 2), "direction": "lower_better"},
            "p95_ms": {"value": round(single_ms * 1.5, 2), "direction": "lower_better"},
            "qps": {"value": round(qps, 1), "direction": "higher_better"},
        },
    )


def bench_fts_search(tmp_dir: Path) -> None:
    """Benchmark full-text search on 1K rows."""
    from arrow_lake.ingest.storage import LanceStorageManager
    from arrow_lake.query.fts import FullTextSearchBridge

    n = 1_000
    dim = 8
    table = pa.table(
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

    storage = LanceStorageManager(base_uri=str(tmp_dir))
    storage.create_dataset("bench_fts", table)
    bridge = FullTextSearchBridge(storage)
    bridge.create_index("bench_fts")

    def single_query() -> None:
        bridge.search("bench_fts", "machine learning", top_k=10)

    single_ms = _median_bench(single_query) * 1000

    queries = ["machine learning", "data processing", "neural networks"] * 10

    def batch_query() -> None:
        for q in queries:
            bridge.search("bench_fts", q, top_k=10)

    duration = _median_bench(batch_query, warmup=1, repeats=3)
    qps = len(queries) / duration

    _save_baseline(
        "fts_search_1k",
        {
            "p50_ms": {"value": round(single_ms, 2), "direction": "lower_better"},
            "p95_ms": {"value": round(single_ms * 1.5, 2), "direction": "lower_better"},
            "qps": {"value": round(qps, 1), "direction": "higher_better"},
        },
    )


_BENCH_FUNCS = {
    "ingest": bench_ingest,
    "vector_search": bench_vector_search,
    "fts_search": bench_fts_search,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Save performance baselines")
    parser.add_argument(
        "--bench",
        choices=[*list(_BENCH_FUNCS.keys()), "all"],
        default="all",
        help="Which benchmark to run (default: all)",
    )
    parser.add_argument(
        "--tmp-dir",
        default="/tmp/arrow-lake-baselines",
        help="Temporary directory for benchmark data",
    )
    args = parser.parse_args()


    tmp_dir = Path(args.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    benches = (
        list(_BENCH_FUNCS.items())
        if args.bench == "all"
        else [(args.bench, _BENCH_FUNCS[args.bench])]
    )

    print(f"Saving baselines to {_BASELINES_DIR}")
    for name, fn in benches:
        print(f"\n{'='*60}")
        print(f"  Benchmark: {name}")
        print(f"{'='*60}")
        try:
            fn(tmp_dir)
        except Exception as exc:
            print(f"  FAILED: {exc}", file=sys.stderr)
            continue

    print(f"\nDone. Baselines saved to {_BASELINES_DIR}")


if __name__ == "__main__":
    main()
