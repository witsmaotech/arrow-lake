"""OLAP analytical-query benchmarks — measures the DuckDB SQL hot path.

These benchmarks exercise :class:`OlapSearchBridge.query`, the same path the
``/api/v1/query/olap`` endpoint and ``Lake.olap_query``/``Lake.sql_query``
facade methods use: register a Lance dataset as a DuckDB view → run SELECT →
return Arrow. The workload is the classic US airline on-time analytical suite
(``ontime`` dataset shapes): filter+order+limit, single-key GROUP BY, string
concat + HAVING, and multi-key GROUP BY.

The dataset is synthetic but schema-faithful (carrier / airport / delay
columns with realistic cardinality), generated with a fixed seed so numbers
are reproducible. No vectors, no index — this isolates the OLAP scan+agg cost.

Run::

    .venv/bin/pytest tests/benchmark/test_bench_olap.py -m benchmark -s
"""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from tests.benchmark.benchmark_report import BenchmarkReport

# Carriers / airports with realistic cardinality so GROUP BY produces
# meaningful groups (not 1-row degenerate groups).
_CARRIERS = ["AA", "UA", "DL", "WN", "OO", "EV", "B6", "AS", "NK", "F9", "HA", "G4"]
_AIRPORTS = [
    "ATL", "LAX", "ORD", "DFW", "DEN", "JFK", "SFO", "SEA", "LAS", "MCO",
    "EWR", "BOS", "PHX", "MIA", "IAH", "DTW", "PHL", "MSP", "LGA", "FLL",
]
_CITIES = {a: f"CITY_{a}" for a in _AIRPORTS}


def _make_ontime_table(n: int, seed: int = 42) -> pa.Table:
    """Build a synthetic ``ontime``-schema table with ``n`` rows."""
    rng = np.random.RandomState(seed)
    origin_idx = rng.randint(0, len(_AIRPORTS), n)
    dest_idx = rng.randint(0, len(_AIRPORTS), n)
    airports_arr = np.array(_AIRPORTS)
    cities_arr = np.array([_CITIES[a] for a in _AIRPORTS])
    arr_delay = rng.normal(5.0, 25.0, n).astype(np.float64)
    dep_delay = rng.normal(3.0, 20.0, n).astype(np.float64)
    return pa.table(
        {
            "IATA_CODE_Reporting_Airline": rng.choice(_CARRIERS, n),
            "ArrDelay": arr_delay,
            "ArrDel15": (arr_delay > 15.0).astype(np.int32),
            "Origin": airports_arr[origin_idx],
            "Dest": airports_arr[dest_idx],
            "OriginCityName": cities_arr[origin_idx],
            "DepDelay": dep_delay,
            "Year": rng.choice([2022, 2023], n).astype(np.int32),
            "Month": rng.randint(1, 13, n).astype(np.int32),
            "DayOfWeek": rng.randint(1, 8, n).astype(np.int32),
            "Distance": rng.uniform(100.0, 3000.0, n).astype(np.float64),
            "AirTime": rng.uniform(30.0, 400.0, n).astype(np.float64),
            "Cancelled": (rng.random(n) < 0.02).astype(np.int32),
        }
    )


# Analytical SQL shapes (adapted from the ontime scenario suite).
# All are SELECT-only, no trailing semicolons, quoted identifiers preserve case.
SQL_FILTER_ORDER_LIMIT = (
    'SELECT * FROM "ontime" WHERE "ArrDel15" = 1 '
    'ORDER BY "ArrDelay" DESC LIMIT 100'
)
SQL_GROUPBY_AIRLINE = (
    'SELECT "IATA_CODE_Reporting_Airline" AS airline, '
    'COUNT(*) AS flights, '
    'ROUND(AVG("ArrDelay"), 1) AS avg_arr_delay, '
    'ROUND(100.0 * AVG(CASE WHEN "ArrDel15" = 1 THEN 1 ELSE 0 END), 1) AS delay15_pct '
    'FROM "ontime" GROUP BY airline ORDER BY delay15_pct DESC LIMIT 10'
)
SQL_ROUTES_HAVING = (
    'SELECT "Origin" || \'-\' || "Dest" AS route, '
    'COUNT(*) AS flights, ROUND(AVG("ArrDelay"), 1) AS avg_arr_delay '
    'FROM "ontime" GROUP BY route HAVING COUNT(*) > 50 '
    'ORDER BY avg_arr_delay DESC LIMIT 10'
)
SQL_MONTHLY_TREND = (
    'SELECT "Year", "Month", COUNT(*) AS flights, '
    'ROUND(AVG("ArrDelay"), 1) AS avg_arr_delay '
    'FROM "ontime" GROUP BY "Year", "Month" ORDER BY "Year", "Month"'
)


@pytest.mark.benchmark
@pytest.mark.parametrize("n", [10_000, 100_000], ids=["10k", "100k"])
class TestOlapBenchmark:
    """Benchmark OLAP analytical query latency and throughput."""

    def test_olap_filter_order_limit(self, n: int, lance_tmp_dir: str) -> None:
        """Point filter + ORDER BY + LIMIT (top delayed flights)."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.olap import OlapSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        storage.create_dataset("ontime", _make_ontime_table(n))
        bridge = OlapSearchBridge(storage)

        report = BenchmarkReport(f"olap_filter_order_limit_{n}")
        elapsed = report.measure(
            f"filter+order+limit ({n:,} rows)",
            lambda: bridge.query("ontime", SQL_FILTER_ORDER_LIMIT),
            rows=n,
            repeats=10,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_olap_groupby_airline(self, n: int, lance_tmp_dir: str) -> None:
        """Single-key GROUP BY aggregation (carrier delay stats)."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.olap import OlapSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        storage.create_dataset("ontime", _make_ontime_table(n))
        bridge = OlapSearchBridge(storage)

        report = BenchmarkReport(f"olap_groupby_airline_{n}")
        elapsed = report.measure(
            f"group-by airline ({n:,} rows)",
            lambda: bridge.query("ontime", SQL_GROUPBY_AIRLINE),
            rows=n,
            repeats=10,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_olap_routes_having(self, n: int, lance_tmp_dir: str) -> None:
        """String concat + GROUP BY + HAVING (most-delayed routes)."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.olap import OlapSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        storage.create_dataset("ontime", _make_ontime_table(n))
        bridge = OlapSearchBridge(storage)

        report = BenchmarkReport(f"olap_routes_having_{n}")
        elapsed = report.measure(
            f"routes group-by having ({n:,} rows)",
            lambda: bridge.query("ontime", SQL_ROUTES_HAVING),
            rows=n,
            repeats=10,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0

    def test_olap_monthly_trend(self, n: int, lance_tmp_dir: str) -> None:
        """Multi-key GROUP BY (year × month delay trend)."""
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.olap import OlapSearchBridge

        storage = LanceStorageManager(base_uri=lance_tmp_dir)
        storage.create_dataset("ontime", _make_ontime_table(n))
        bridge = OlapSearchBridge(storage)

        report = BenchmarkReport(f"olap_monthly_trend_{n}")
        elapsed = report.measure(
            f"multi-key group-by year×month ({n:,} rows)",
            lambda: bridge.query("ontime", SQL_MONTHLY_TREND),
            rows=n,
            repeats=10,
        )
        report.print_summary()
        print(report.to_json())
        assert elapsed > 0
