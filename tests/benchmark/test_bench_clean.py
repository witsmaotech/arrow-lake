"""Clean / writeback pipeline benchmarks — measures the tidy-clean hot path.

The ``POST /api/v1/datasets/{name}/clean`` endpoint (``api/routers/cleaning.py``)
does four things: read the Lance dataset into Arrow, register it with DuckDB,
run a transform SELECT, then write the result back via ``restore_dataset``
(drop + recreate). This benchmark replicates that exact in-process path so the
numbers reflect what a real clean operation costs — and decomposes it to show
where the time goes (read vs. transform vs. writeback).

There is intentionally no REST hop and no Gravitino/audit side-effect: this
isolates the storage + DuckDB mechanics. ``restore_dataset`` is the same call
the router makes for rollback and schema change.

Run::

    .venv/bin/pytest tests/benchmark/test_bench_clean.py -m benchmark -s
"""



from __future__ import annotations

import pytest

pytestmark = pytest.mark.benchmark

import numpy as np
import pyarrow as pa
import pytest

from tests.benchmark.benchmark_report import BenchmarkReport

# Transform SQL mirrors a realistic clean step list: case normalization,
# type cast, fillna, and a predicate filter — composed as one DuckDB SELECT.
_CLEAN_SQL = (
    "SELECT lower(category) AS category, "
    "CAST(value AS DOUBLE) AS value, "
    "COALESCE(flag, 0) AS flag, "
    "id "
    "FROM t WHERE value > 0"
)


def _make_clean_table(n: int, seed: int = 42) -> pa.Table:
    """Build an ``n``-row table with a string key, category, value, flag."""
    rng = np.random.RandomState(seed)
    categories = np.array(["Alpha", "Beta", "Gamma", "Delta", "Alpha", "Beta"])
    return pa.table(
        {
            "id": [f"row_{i:06d}" for i in range(n)],
            "category": rng.choice(categories, n),
            "value": rng.normal(50.0, 20.0, n).astype(np.float64),
            "flag": rng.randint(0, 3, n).astype(np.int32),
        }
    )


@pytest.mark.benchmark
@pytest.mark.parametrize("n", [10_000, 100_000], ids=["10k", "100k"])
class TestCleanBenchmark:
    """Benchmark the clean/writeback pipeline end-to-end and decomposed."""

    def test_clean_full_pipeline(self, n: int, lance_tmp_dir: str) -> None:
        """Full clean: read → DuckDB transform → restore_dataset writeback."""
        import duckdb

        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        storage.create_dataset("clean_bench", _make_clean_table(n))

        def _full_cycle() -> None:
            table = storage.read_dataset("clean_bench")
            con = duckdb.connect()
            try:
                con.register("t", table)
                cleaned = con.sql(_CLEAN_SQL).to_arrow_table()
            finally:
                con.close()
            storage.restore_dataset("clean_bench", cleaned)

        report = BenchmarkReport(f"clean_full_pipeline_{n}")
        elapsed = report.measure(
            f"read→transform→writeback ({n:,} rows)",
            _full_cycle,
            rows=n,
            repeats=5,
            warmup=1,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_clean_decomposed(self, n: int, lance_tmp_dir: str) -> None:
        """Decompose the pipeline: read / transform / writeback measured separately."""
        import duckdb

        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        storage.create_dataset("clean_dec", _make_clean_table(n))
        table = storage.read_dataset("clean_dec")

        report = BenchmarkReport(f"clean_decomposed_{n}")

        # 1. Read (Lance → Arrow)
        report.measure(
            f"read dataset ({n:,} rows)",
            lambda: storage.read_dataset("clean_dec"),
            rows=n,
            repeats=8,
        )

        # 2. Transform (DuckDB SELECT over registered Arrow)
        def _transform() -> None:
            con = duckdb.connect()
            try:
                con.register("t", table)
                con.sql(_CLEAN_SQL).to_arrow_table()
            finally:
                con.close()

        report.measure(
            f"duckdb transform ({n:,} rows)",
            _transform,
            rows=n,
            repeats=8,
        )

        # 3. Writeback (drop + recreate). Rebuild the cleaned table once for write input.
        con = duckdb.connect()
        try:
            con.register("t", table)
            cleaned = con.sql(_CLEAN_SQL).to_arrow_table()
        finally:
            con.close()

        report.measure(
            f"restore_dataset write ({n:,} rows)",
            lambda: storage.restore_dataset("clean_dec", cleaned),
            rows=n,
            repeats=5,
            warmup=1,
        )

        report.print_summary()
        print(report.to_json())
