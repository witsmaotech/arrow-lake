"""IVF_PQ vector index BUILD benchmarks (v1.10.2 §2.3 / review H2).

The four delivered benchmarks measure SEARCH, not BUILD. Every ingest append
triggers ``create_vector_index(replace=True)`` → a full IVF_PQ rebuild on the
request path. This quantifies build cost at 10k / 100k rows to decide whether
the append-path sync rebuild is acceptable or must move off the request path
(§5.3 P1.4 — null-row backfill async threshold).

Zero external deps (lancedb + numpy + pyarrow only — no embed backend, no LLM).
Run::

    .venv/bin/python3 -m pytest tests/benchmark/test_bench_index_build.py -m benchmark -s
"""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest
import lancedb

from tests.benchmark.benchmark_report import BenchmarkReport

_DIM = 1024  # text-embedding-v3 / bge-m3 dimension (most common)


def _vector_table(n_rows: int, dim: int = _DIM) -> pa.Table:
    """Synthetic table with a random FixedSizeList embedding column."""
    rng = np.random.default_rng(42)
    flat = rng.standard_normal((n_rows, dim)).astype(np.float32).reshape(-1)
    arr = pa.FixedSizeListArray.from_arrays(pa.array(flat), dim)
    return pa.table(
        {
            "id": pa.array(range(n_rows)),
            "text_content": pa.array(["x"] * n_rows),
            "text_embedding": arr,
        }
    )


def _create_ivf_pq(tbl) -> None:
    """Create an IVF_PQ index; accept both with/without explicit index_type."""
    try:
        tbl.create_index(
            metric="L2",
            vector_column_name="text_embedding",
            index_type="IVF_PQ",
            replace=True,
        )
    except TypeError:
        # older/newer lancedb signature without index_type kw
        tbl.create_index(
            metric="L2", vector_column_name="text_embedding", replace=True
        )


@pytest.mark.benchmark
class TestIndexBuildBenchmark:
    """Measure IVF_PQ build (create+replace) latency vs row count."""

    def test_ivf_pq_build_10k(self, tmp_path) -> None:
        n = 10_000
        db = lancedb.connect(str(tmp_path))
        tbl = db.create_table("t", _vector_table(n), mode="overwrite")
        report = BenchmarkReport("ivf_pq_build_10k")
        elapsed = report.measure(
            "IVF_PQ create+replace (10k rows, dim=1024)",
            lambda: _create_ivf_pq(tbl),
            rows=n,
            repeats=1,
        )
        report.print_summary()
        print(report.to_json())
        print(f"[meta] throughput: {n / elapsed:,.0f} rows/s")
        assert elapsed > 0

    def test_ivf_pq_build_100k(self, tmp_path) -> None:
        n = 100_000
        db = lancedb.connect(str(tmp_path))
        tbl = db.create_table("t", _vector_table(n), mode="overwrite")
        report = BenchmarkReport("ivf_pq_build_100k")
        elapsed = report.measure(
            "IVF_PQ create+replace (100k rows, dim=1024)",
            lambda: _create_ivf_pq(tbl),
            rows=n,
            repeats=1,
        )
        report.print_summary()
        print(report.to_json())
        print(f"[meta] throughput: {n / elapsed:,.0f} rows/s")
        assert elapsed > 0

    def test_ivf_pq_build_below_training_floor_skipped(self, tmp_path) -> None:
        """< 256 rows cannot train IVF_PQ — documents the floor (§2.3)."""
        n = 200
        db = lancedb.connect(str(tmp_path))
        tbl = db.create_table("t", _vector_table(n), mode="overwrite")
        report = BenchmarkReport("ivf_pq_build_200_below_floor")
        try:
            report.measure(
                "IVF_PQ create (200 rows — below PQ training floor)",
                lambda: _create_ivf_pq(tbl),
                rows=n,
                repeats=1,
            )
            # if it somehow succeeded, just report
            report.print_summary()
            print(report.to_json())
        except Exception as exc:  # noqa: BLE001 — expected below floor
            pytest.skip(f"IVF_PQ below training floor (expected): {str(exc)[:120]}")
