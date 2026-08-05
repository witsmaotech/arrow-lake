"""Arrow Lake performance benchmarks — Phase 6.2.

Measures DuckDB query latency, connection pool throughput, and
document chunking speed. Outputs results as structured JSON and
a human-readable summary table.

Usage:
    python -m examples.query.benchmark [--iterations 100] [--output bench.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _time_it(fn, *args, iterations: int = 50, warmup: int = 5, **kwargs) -> dict[str, Any]:
    """Run *fn* multiple times and return timing statistics."""
    for _ in range(warmup):
        fn(*args, **kwargs)

    times: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn(*args, **kwargs)
        times.append(time.perf_counter() - t0)

    if not times:
        return {"mean_ms": 0, "p50_ms": 0, "p99_ms": 0, "min_ms": 0, "max_ms": 0, "ops_per_sec": 0}

    ms = [t * 1000 for t in times]
    ms.sort()
    mean = statistics.mean(ms)
    return {
        "mean_ms": round(mean, 3),
        "p50_ms": round(ms[len(ms) // 2], 3),
        "p99_ms": round(ms[int(len(ms) * 0.99)], 3),
        "min_ms": round(min(ms), 3),
        "max_ms": round(max(ms), 3),
        "ops_per_sec": round(1000 / mean, 1),
        "iterations": iterations,
    }


def bench_duckdb_query(iterations: int) -> dict[str, Any]:
    """Benchmark DuckDB SELECT query latency."""
    import duckdb

    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE bench AS SELECT range AS id, 'doc_' || range AS name, random()::FLOAT AS score FROM range(10000)")

    def _query() -> None:
        conn.execute("SELECT id, name, score FROM bench WHERE score > 0.5 ORDER BY score DESC LIMIT 100").fetchall()

    result = _time_it(_query, iterations=iterations)
    conn.close()
    return {"name": "duckdb_select_filter_order_limit_10k_rows", **result}


def bench_duckdb_aggregation(iterations: int) -> dict[str, Any]:
    """Benchmark DuckDB GROUP BY aggregation."""
    import duckdb

    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE bench AS SELECT (range % 100) AS category, random()::FLOAT AS score FROM range(10000)")

    def _agg() -> None:
        conn.execute("SELECT category, count(*), avg(score), max(score) FROM bench GROUP BY category ORDER BY category").fetchall()

    result = _time_it(_agg, iterations=iterations)
    conn.close()
    return {"name": "duckdb_group_by_aggregation_10k_rows", **result}


def bench_duckdb_fts_like(iterations: int) -> dict[str, Any]:
    """Benchmark DuckDB string search (FTS-like pattern matching)."""
    import duckdb

    conn = duckdb.connect(":memory:")
    rows = [(i, f"document about machine learning and AI topic {i % 50}") for i in range(5000)]
    conn.execute("CREATE TABLE bench(id INTEGER, text VARCHAR)")
    conn.executemany("INSERT INTO bench VALUES (?, ?)", rows)

    def _search() -> None:
        conn.execute("SELECT id, text FROM bench WHERE text LIKE '%machine learning%' LIMIT 20").fetchall()

    result = _time_it(_search, iterations=iterations)
    conn.close()
    return {"name": "duckdb_string_like_search_5k_rows", **result}


def bench_session_pool(iterations: int) -> dict[str, Any]:
    """Benchmark DuckDB connection open/close cycle."""
    import duckdb

    def _open_query_close() -> None:
        conn = duckdb.connect(":memory:")
        conn.execute("SELECT 1").fetchall()
        conn.close()

    result = _time_it(_open_query_close, iterations=iterations)
    return {"name": "duckdb_connection_open_close", **result}


def bench_chunking(iterations: int) -> dict[str, Any]:
    """Benchmark text chunking throughput."""
    from arrow_lake.ingest.chunker import DocumentChunker, ChunkStrategy

    chunker = DocumentChunker(strategy=ChunkStrategy.RECURSIVE, chunk_size=512, chunk_overlap=50)
    sample_paragraph = ("This is a sample document about machine learning and artificial intelligence. "
                        "It contains multiple sentences that discuss various topics including neural networks, "
                        "deep learning, natural language processing, and computer vision. "
                        "The document is designed to be representative of typical text data that would be "
                        "chunked and embedded for retrieval augmented generation systems. ")
    pages = [(i, sample_paragraph * (i % 5 + 1)) for i in range(20)]

    def _chunk() -> None:
        chunker.chunk(pages)

    result = _time_it(_chunk, iterations=iterations)
    return {"name": "document_chunking_recursive_512_50_20pages", **result}


def bench_validation(iterations: int) -> dict[str, Any]:
    """Benchmark SQL validation throughput."""
    from arrow_lake.validation import validate_sql_safety, validate_identifier, escape_sql_literal

    def _validate() -> None:
        validate_identifier("my_dataset_2024")
        validate_sql_safety("SELECT * FROM my_table WHERE id = 1")
        escape_sql_literal("normal text with 'quotes' and \\backslashes\\")

    result = _time_it(_validate, iterations=iterations * 5)
    return {"name": "input_validation_sql_identifier_escape", **result}


def bench_token_counting(iterations: int) -> dict[str, Any]:
    """Benchmark token counting (heuristic fallback, no tiktoken dependency)."""
    from arrow_lake.rag.context import count_tokens

    sample = ("This is a sample text for token counting benchmark. It contains English words and "
              "some Chinese characters like 机器学习 and 自然语言处理. "
              "The function should handle mixed content efficiently. ") * 20

    def _count() -> None:
        count_tokens(sample)

    result = _time_it(_count, iterations=iterations * 10)
    return {"name": "token_counting_heuristic", **result}


BENCHMARKS = [
    ("DuckDB SELECT", bench_duckdb_query),
    ("DuckDB Aggregation", bench_duckdb_aggregation),
    ("DuckDB String Search", bench_duckdb_fts_like),
    ("Session Pool", bench_session_pool),
    ("Text Chunking", bench_chunking),
    ("Input Validation", bench_validation),
    ("Token Counting", bench_token_counting),
]


def run_all(iterations: int = 50) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name, bench_fn in BENCHMARKS:
        try:
            print(f"  Running: {name}...", end=" ", flush=True)
            result = bench_fn(iterations)
            result["status"] = "ok"
            results.append(result)
            print(f"{result['mean_ms']:.2f}ms avg ({result['ops_per_sec']:.0f} ops/s)")
        except Exception as exc:
            print(f"SKIPPED ({exc})")
            results.append({"name": bench_fn.__name__, "status": "skipped", "error": str(exc)})
    return results


def print_summary(results: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 78)
    print(f"{'Benchmark':<42} {'Mean':>8} {'P50':>8} {'P99':>8} {'Ops/s':>8}")
    print("-" * 78)
    for r in results:
        if r.get("status") != "ok":
            print(f"{r['name']:<42} {'SKIPPED':>8}")
            continue
        print(f"{r['name']:<42} {r['mean_ms']:>7.2f}ms {r['p50_ms']:>7.2f}ms {r['p99_ms']:>7.2f}ms {r['ops_per_sec']:>8.0f}")
    print("=" * 78)


def main() -> None:
    parser = argparse.ArgumentParser(description="Arrow Lake performance benchmarks")
    parser.add_argument("--iterations", type=int, default=50, help="Iterations per benchmark (default: 50)")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file path")
    args = parser.parse_args()

    print(f"Arrow Lake Performance Benchmark (iterations={args.iterations})")
    print("-" * 78)
    results = run_all(args.iterations)
    print_summary(results)

    if args.output:
        output = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "iterations": args.iterations, "results": results}
        Path(args.output).write_text(json.dumps(output, indent=2, ensure_ascii=False))
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
