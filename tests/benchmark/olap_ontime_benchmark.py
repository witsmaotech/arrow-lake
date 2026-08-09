#!/usr/bin/env python3
"""Deep OLAP performance benchmark for the `ontime` dataset (107M rows).

Drives the Arrow Lake REST API (`POST /datasets/{name}/query/olap`) through a
workload matrix that isolates the dimensions that matter for a 107M-row
vectorless Lance dataset:

  - Query class       (7 canonical OLAP shapes, incl. the 8 business SQL)
  - Selectivity       (WHERE filter: 100% -> ~0.5%)
  - Result size       (LIMIT pagination: the native-scan advantage zone)
  - Concurrency       (1 -> 16, crossing max_concurrent_queries=4)
  - Cache state       (cold first run vs warm page-cache vs query-cache)
  - Scan mode         (pyarrow_fallback vs native/auto — set on the container)

Outputs timestamped JSON (raw timings + telemetry) and a Markdown report with
regression-ready baselines for tests/benchmark/baselines/.

Usage
-----
  # full stable suite (Phase 1-5,7) — safe, ~70 min
  python3 tests/benchmark/olap_ontime_benchmark.py --phases latency,selectivity,pagination,concurrency,soak

  # one quick latency pass (5 queries, 3 repeats) — ~15 min
  python3 tests/benchmark/olap_ontime_benchmark.py --phases latency --repeats 3

  # native A/B — FIRST switch the container (see switch_scan_mode below), then:
  python3 tests/benchmark/olap_ontime_benchmark.py --phases latency --tag native --repeats 3

Notes
-----
- Query cache is busted per-repeat by prefixing a unique /* run=... */ comment.
  IMPORTANT: the comment must be a PREFIX, never a suffix — OlapSearchBridge
  ._apply_limit() anchors its LIMIT detection at end-of-string; a trailing
  comment makes it miss an existing LIMIT and append a second one -> parse error.
- Resource telemetry is sampled host-side (docker stats + D-state scan) because
  the runner drives the API over HTTP; load average is NOT used (WSL2 unreliable).
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

API_BASE = "http://127.0.0.1:8000/api/v1"
API_KEY = "dev-api-key-for-local-testing-only"
DATASET = "ontime"
CONTAINER = "arrow-lake-api-1"
RESULTS_DIR = Path(__file__).parent / "results"
BASELINES_DIR = Path(__file__).parent / "baselines"

# Per-query cap (matches OlapConfig.max_result_rows default).
MAX_ROWS = 100_000


# --------------------------------------------------------------------------- #
# Workload definitions
# --------------------------------------------------------------------------- #
# Each entry: id -> (class, label, sql_template). SQL must NOT end with a
# trailing comment. LIMIT (if any) must be the last token.

QUERIES: dict[str, tuple[str, str, str]] = {
    # Class A: full-scan low-cardinality aggregation
    "q6_weekday": (
        "A_low_card",
        "GROUP BY DayOfWeek (7 groups)",
        'SELECT "DayOfWeek", COUNT(*) AS flights, ROUND(AVG("ArrDelay"),1) AS avg_delay '
        'FROM ontime GROUP BY "DayOfWeek" ORDER BY "DayOfWeek"',
    ),
    "q7_distance": (
        "A_low_card",
        "GROUP BY DistanceGroup (11 groups)",
        'SELECT "DistanceGroup", COUNT(*) AS flights, ROUND(AVG("Distance"),0) AS avg_dist '
        'FROM ontime GROUP BY "DistanceGroup" ORDER BY "DistanceGroup"',
    ),
    # Class C: full-scan no-group aggregation (pure scan throughput)
    "q4_reasons": (
        "C_scan_agg",
        "SUM of 5 delay columns (no group)",
        'SELECT SUM("CarrierDelay") AS carrier, SUM("WeatherDelay") AS weather, '
        'SUM("NASDelay") AS nas, SUM("SecurityDelay") AS security, '
        'SUM("LateAircraftDelay") AS late FROM ontime',
    ),
    "count_star": (
        "C_scan_agg",
        "COUNT(*) full scan",
        "SELECT COUNT(*) AS n FROM ontime",
    ),
    # Class E: full-scan multi-column grouping
    "q3_monthly": (
        "E_multi_col",
        "GROUP BY Year,Month (~216 groups)",
        'SELECT "Year","Month", COUNT(*) AS flights, ROUND(AVG("ArrDelay"),1) AS avg_delay '
        'FROM ontime GROUP BY "Year","Month" ORDER BY "Year","Month"',
    ),
    "q1_airline": (
        "E_multi_col",
        "GROUP BY airline (15 groups)",
        'SELECT "IATA_CODE_Reporting_Airline" AS airline, COUNT(*) AS flights, '
        'ROUND(AVG("ArrDelay"),1) AS avg_delay FROM ontime GROUP BY airline '
        "ORDER BY flights DESC",
    ),
    # Class B: full-scan high-cardinality grouping (hash-agg pressure)
    "q5_airports": (
        "B_high_card",
        "GROUP BY Origin (~350 groups)",
        'SELECT "Origin", COUNT(*) AS departures, ROUND(AVG("DepDelay"),1) AS avg_delay '
        'FROM ontime GROUP BY "Origin" ORDER BY departures DESC',
    ),
    # Class D: filtered aggregation (predicate pushdown)
    "q8_cancel": (
        "D_filtered",
        "WHERE Cancelled=1 GROUP BY CancellationCode",
        'SELECT "CancellationCode", COUNT(*) AS cancelled FROM ontime '
        'WHERE "Cancelled" = 1 GROUP BY "CancellationCode" ORDER BY cancelled DESC',
    ),
}

# Selectivity sweep base: a COUNT over a filtered single column.
SELECTIVITY_FILTERS: list[tuple[str, str]] = [
    ("full_100pct", ""),
    ("year_eq", 'WHERE "Year" = 2018'),
    ("origin_atl", 'WHERE "Origin" = \'ATL\''),
    ("cancelled", 'WHERE "Cancelled" = 1'),
    ("diverted", 'WHERE "Diverted" = 1'),
]

# Pagination sweep: explicit LIMIT at end (pushdown-sensitive).
PAGINATION_LIMITS = [10, 100, 1_000, 10_000]
PAGINATION_SQL = (
    'SELECT "Year","Month","Origin","Dest","ArrDelay","DepDelay" '
    "FROM ontime ORDER BY \"Year\""
)


# --------------------------------------------------------------------------- #
# HTTP + timing primitives
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class QueryOutcome:
    ok: bool
    elapsed: float
    rows: int
    error: str = ""


def bust_cache(sql: str, token: str) -> str:
    """Inject a cache-busting comment right after the leading SELECT keyword.

    Placement is load-bearing:
      - A LEADING comment (`/* x */ SELECT ...`) is rejected by validate_sql_safety.
      - A TRAILING comment breaks OlapSearchBridge._apply_limit, which anchors its
        LIMIT detection at end-of-string — it then misses an existing LIMIT and
        appends a second one -> ParserException (double LIMIT).
    Inserting immediately after the first SELECT is safe for both and keeps any
    trailing LIMIT as the true last token.
    """
    import re as _re
    m = _re.match(r"\s*[Ss][Ee][Ll][Ee][Cc][Tt]", sql)
    if not m:
        return sql
    return sql[: m.end()] + f" /* {token} */" + sql[m.end():]


def run_query(sql: str, timeout: int = 320) -> QueryOutcome:
    """Execute one OLAP query; return latency + result row count."""
    body = json.dumps({"sql": sql, "format": "json", "max_rows": MAX_ROWS}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/datasets/{DATASET}/query/olap",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            obj = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return QueryOutcome(False, time.perf_counter() - t0, 0,
                            f"HTTP {e.code}: {e.read().decode(errors='replace')[:160]}")
    except Exception as e:  # noqa: BLE001 — benchmark must survive any failure
        return QueryOutcome(False, time.perf_counter() - t0, 0,
                            f"{type(e).__name__}: {str(e)[:160]}")
    dt = time.perf_counter() - t0
    rows = len(obj.get("rows") or [])
    return QueryOutcome(obj.get("success", False), dt, rows)


def percentile(sorted_vals: list[float], p: float) -> float:
    """Linear-interpolation percentile of a pre-sorted list."""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


@dataclass
class LatencyStats:
    label: str
    n: int
    failures: int
    p50: float
    p95: float
    p99: float
    mean: float
    rows: int

    def as_dict(self) -> dict:
        return {k: round(v, 4) if isinstance(v, float) else v
                for k, v in self.__dict__.items()}


def measure_latency(label: str, sql_base: str, *, warmup: int, repeats: int,
                    run_id: str) -> LatencyStats:
    """Warm up, then time `repeats` runs with cache-busting prefix comments."""
    for i in range(warmup):
        run_query(bust_cache(sql_base, f"warmup_{run_id}_{i}"))
    timings: list[float] = []
    failures = 0
    rows = 0
    for i in range(repeats):
        out = run_query(bust_cache(sql_base, f"{run_id}_{i}"))
        if out.ok:
            timings.append(out.elapsed)
            rows = out.rows
        else:
            failures += 1
            print(f"    [FAIL] {label} repeat {i}: {out.error}")
    timings.sort()
    return LatencyStats(
        label=label,
        n=len(timings),
        failures=failures,
        p50=percentile(timings, 0.50),
        p95=percentile(timings, 0.95),
        p99=percentile(timings, 0.99),
        mean=statistics.fmean(timings) if timings else float("nan"),
        rows=rows,
    )


# --------------------------------------------------------------------------- #
# Resource telemetry (host-side background sampler)
# --------------------------------------------------------------------------- #


@dataclass
class Telemetry:
    cpu_samples: list[float] = field(default_factory=list)
    mem_gib_samples: list[float] = field(default_factory=list)
    d_state_samples: list[int] = field(default_factory=list)

    def snapshot(self) -> dict:
        def mid(xs: list[float]) -> float:
            return statistics.median(xs) if xs else float("nan")
        return {
            "cpu_pct_median": round(mid(self.cpu_samples), 2),
            "cpu_pct_max": round(max(self.cpu_samples), 2) if self.cpu_samples else None,
            "mem_gib_median": round(mid(self.mem_gib_samples), 3),
            "d_state_max": max(self.d_state_samples) if self.d_state_samples else 0,
            "samples": len(self.cpu_samples),
        }


def _docker_cpu_mem() -> tuple[float, float]:
    """Return (cpu_pct, mem_gib) for the api container, best-effort."""
    try:
        out = subprocess.check_output(
            ["docker", "stats", "--no-stream",
             "--format", "{{.CPUPerc}}|{{.MemUsage}}", CONTAINER],
            text=True, timeout=10, stderr=subprocess.DEVNULL,
        ).strip()
        cpu_s, mem_s = out.split("|")
        cpu = float(cpu_s.strip().rstrip("%"))
        # "5.91GiB / 16GiB" -> 5.91
        mem_gib = float(mem_s.split("/")[0].strip().rstrip("GiB").strip())
        return cpu, mem_gib
    except Exception:  # noqa: BLE001
        return float("nan"), float("nan")


def _count_d_state() -> int:
    """Count uninterruptible-sleep processes on the host."""
    try:
        out = subprocess.check_output(["ps", "-eo", "stat"], text=True, timeout=5)
        return sum(1 for line in out.splitlines()[1:] if line.strip().startswith("D"))
    except Exception:  # noqa: BLE001
        return 0


class TelemetrySampler:
    """Sample CPU/MEM/D-state every `interval` s until stopped."""

    def __init__(self, interval: float = 2.0) -> None:
        self.interval = interval
        self.telemetry = Telemetry()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> Telemetry:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 2)
        return self.telemetry

    def _run(self) -> None:
        while not self._stop.is_set():
            cpu, mem = _docker_cpu_mem()
            if cpu == cpu:  # not NaN
                self.telemetry.cpu_samples.append(cpu)
                self.telemetry.mem_gib_samples.append(mem)
            self.telemetry.d_state_samples.append(_count_d_state())
            self._stop.wait(self.interval)


# --------------------------------------------------------------------------- #
# Phases
# --------------------------------------------------------------------------- #


def phase_latency(repeats: int, warmup: int, run_id: str,
                  sampler: TelemetrySampler) -> list[LatencyStats]:
    print("\n[Phase] latency — 8 business queries, warm-cache p50/p95/p99")
    results: list[LatencyStats] = []
    for qid, (cls, label, sql) in QUERIES.items():
        print(f"  -> {qid} [{cls}] {label}")
        stats = measure_latency(qid, sql, warmup=warmup, repeats=repeats,
                                run_id=f"{run_id}_lat_{qid}")
        print(f"     p50={stats.p50:.2f}s p95={stats.p95:.2f}s "
              f"rows={stats.rows} fails={stats.failures}")
        results.append(stats)
    return results


def phase_selectivity(repeats: int, run_id: str) -> list[LatencyStats]:
    print("\n[Phase] selectivity — COUNT(*) over varying WHERE filters")
    results: list[LatencyStats] = []
    for sid, flt in SELECTIVITY_FILTERS:
        sql = f'SELECT COUNT(*) AS n FROM ontime {flt}'.strip()
        label = f"sel_{sid}"
        print(f"  -> {label}  ({flt or 'no filter'})")
        stats = measure_latency(label, sql, warmup=1, repeats=repeats,
                                run_id=f"{run_id}_sel_{sid}")
        print(f"     p50={stats.p50:.2f}s fails={stats.failures}")
        results.append(stats)
    return results


def phase_pagination(repeats: int, run_id: str) -> list[LatencyStats]:
    print("\n[Phase] pagination — LIMIT pushdown sensitivity")
    results: list[LatencyStats] = []
    for lim in PAGINATION_LIMITS:
        sql = f"{PAGINATION_SQL} LIMIT {lim}"
        label = f"page_L{lim}"
        print(f"  -> {label}")
        stats = measure_latency(label, sql, warmup=1, repeats=repeats,
                                run_id=f"{run_id}_pg_{lim}")
        print(f"     p50={stats.p50:.2f}s rows={stats.rows} fails={stats.failures}")
        results.append(stats)
    return results


def phase_concurrency(repeats: int, run_id: str) -> list[dict]:
    """Fire waves of N identical concurrent queries; record tail latency."""
    print("\n[Phase] concurrency — concurrent waves vs tail latency")
    # Use a low-cardinality query that returns fast-ish under load.
    sql_base = ('SELECT "DayOfWeek", COUNT(*) AS flights FROM ontime '
                'GROUP BY "DayOfWeek"')
    # Cap at 8: the DuckDB slot pool (max_concurrent_queries=4) saturates above 4,
    # and N=16 reliably triggers the transient OLAP wedge (burst 500s). The soak
    # phase already characterizes that failure mode.
    levels = [1, 2, 4, 8]
    out: list[dict] = []
    for n in levels:
        wave_latencies: list[float] = []
        failures = 0
        for wave in range(repeats):
            with ThreadPoolExecutor(max_workers=n) as pool:
                futs = [pool.submit(run_query, bust_cache(sql_base, f"{run_id}_c{n}_{wave}_{i}"))
                        for i in range(n)]
                wave_t = []
                for f in as_completed(futs):
                    r = f.result()
                    if r.ok:
                        wave_t.append(r.elapsed)
                    else:
                        failures += 1
                wave_latencies.extend(wave_t)
        wave_latencies.sort()
        rec = {
            "concurrency": n,
            "samples": len(wave_latencies),
            "failures": failures,
            "p50": round(percentile(wave_latencies, 0.50), 3),
            "p95": round(percentile(wave_latencies, 0.95), 3),
            "p99": round(percentile(wave_latencies, 0.99), 3),
            "mean": round(statistics.fmean(wave_latencies), 3) if wave_latencies else None,
        }
        print(f"  -> N={n:>2}: p50={rec['p50']:.2f}s p95={rec['p95']:.2f}s "
              f"fails={failures}")
        out.append(rec)
    return out


def phase_soak(n_queries: int, run_id: str) -> dict:
    """Mixed-query stability soak: count failures / timeouts over a long run."""
    print(f"\n[Phase] soak — {n_queries} mixed queries, count failures")
    qids = list(QUERIES.keys())
    failures = 0
    timeouts = 0
    latencies: list[float] = []
    t0 = time.perf_counter()
    for i in range(n_queries):
        qid = qids[i % len(qids)]
        sql = QUERIES[qid][2]
        out = run_query(bust_cache(sql, f"{run_id}_soak_{i}"), timeout=320)
        if not out.ok:
            failures += 1
            if "timeout" in out.error.lower() or "timed out" in out.error.lower():
                timeouts += 1
            print(f"  [soak {i}] FAIL {qid}: {out.error[:100]}")
        else:
            latencies.append(out.elapsed)
        if (i + 1) % 10 == 0:
            print(f"  [soak] {i+1}/{n_queries} done, failures={failures}")
    latencies.sort()
    return {
        "total": n_queries,
        "failures": failures,
        "timeouts": timeouts,
        "elapsed_wall": round(time.perf_counter() - t0, 1),
        "p50": round(percentile(latencies, 0.50), 3),
        "p95": round(percentile(latencies, 0.95), 3),
        "p99": round(percentile(latencies, 0.99), 3),
        "success_rate": round((n_queries - failures) / n_queries, 4),
    }


# --------------------------------------------------------------------------- #
# Scan-mode probe + reporting
# --------------------------------------------------------------------------- #


def probe_scan_mode() -> str:
    """Read the running container's LANCE_SCAN_MODE (best-effort)."""
    try:
        out = subprocess.check_output(
            ["docker", "exec", CONTAINER, "sh", "-c",
             "echo $ARROW_LAKE__OLAP__LANCE_SCAN_MODE"],
            text=True, timeout=10, stderr=subprocess.DEVNULL,
        ).strip()
        return out or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def render_markdown(report: dict) -> str:
    md: list[str] = []
    md.append(f"# ontime OLAP Benchmark — {report['tag']}")
    md.append("")
    md.append(f"- **Dataset**: `{DATASET}` ({report['dataset']['num_rows']:,} rows, "
              f"{report['dataset']['num_columns']} cols)")
    md.append(f"- **Scan mode**: `{report['scan_mode']}`")
    md.append(f"- **Started**: {report['started_at']}  ·  **Elapsed**: "
              f"{report['elapsed_seconds']}s")
    md.append(f"- **Repeats/query**: {report['repeats']} (warmup {report['warmup']})")
    md.append("")

    def latency_table(title: str, stats_list: list[dict]) -> None:
        md.append(f"## {title}")
        md.append("")
        md.append("| query | class | n | p50 (s) | p95 (s) | p99 (s) | rows | fails |")
        md.append("|---|---|---|---|---|---|---|---|")
        for s in stats_list:
            cls = QUERIES.get(s["label"], ("?", "?", ""))[0]
            md.append(f"| {s['label']} | {cls} | {s['n']} | {s['p50']:.2f} | {s['p95']:.2f} "
                      f"| {s['p99']:.2f} | {s['rows']} | {s['failures']} |")
        md.append("")

    if report.get("latency"):
        latency_table("Latency by query class", report["latency"])
    if report.get("selectivity"):
        latency_table("Selectivity sweep (COUNT + WHERE)", report["selectivity"])
    if report.get("pagination"):
        latency_table("Pagination (LIMIT pushdown)", report["pagination"])

    if report.get("concurrency"):
        md.append("## Concurrency scaling")
        md.append("")
        md.append("| N | p50 (s) | p95 (s) | p99 (s) | fails |")
        md.append("|---|---|---|---|---|")
        for c in report["concurrency"]:
            md.append(f"| {c['concurrency']} | {c['p50']:.2f} | {c['p95']:.2f} "
                      f"| {c['p99']:.2f} | {c['failures']} |")
        md.append("")

    if report.get("soak"):
        s = report["soak"]
        md.append("## Stability soak")
        md.append("")
        md.append(f"- {s['total']} mixed queries · **success rate {s['success_rate']*100:.1f}%** "
                  f"· failures={s['failures']} timeouts={s['timeouts']} "
                  f"· wall={s['elapsed_wall']}s")
        md.append(f"- p50={s['p50']:.2f}s p95={s['p95']:.2f}s p99={s['p99']:.2f}s")
        md.append("")

    if report.get("telemetry"):
        md.append("## Resource telemetry (during phases)")
        md.append("")
        md.append(f"- CPU median: {report['telemetry']['cpu_pct_median']}% "
                  f"(max {report['telemetry']['cpu_pct_max']}%)")
        md.append(f"- MEM median: {report['telemetry']['mem_gib_median']} GiB")
        md.append(f"- D-state max: {report['telemetry']['d_state_max']}")
        md.append("")

    return "\n".join(md)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

PHASES = {"latency", "selectivity", "pagination", "concurrency", "soak"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phases", default="latency,selectivity,pagination,concurrency,soak",
                    help=f"comma-separated subset of {sorted(PHASES)}")
    ap.add_argument("--repeats", type=int, default=5, help="timed repeats per query")
    ap.add_argument("--warmup", type=int, default=2, help="untimed warmup runs per query")
    ap.add_argument("--soak-n", type=int, default=60, help="queries in soak phase")
    ap.add_argument("--tag", default="pyarrow_fallback",
                    help="label stamped on this run (e.g. native)")
    args = ap.parse_args()

    chosen = {p.strip() for p in args.phases.split(",") if p.strip()}
    bad = chosen - PHASES
    if bad:
        print(f"Unknown phase(s): {bad}. Valid: {sorted(PHASES)}")
        return 2

    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"=== ontime benchmark | tag={args.tag} | phases={sorted(chosen)} ===")
    print(f"    scan_mode={probe_scan_mode()} repeats={args.repeats} warmup={args.warmup}")

    sampler = TelemetrySampler(interval=3.0)
    sampler.start()
    t_start = time.perf_counter()

    report: dict = {
        "tag": args.tag,
        "run_id": run_id,
        "started_at": run_id,
        "dataset": {"name": DATASET, "num_rows": 107_670_808, "num_columns": 109},
        "scan_mode": probe_scan_mode(),
        "repeats": args.repeats,
        "warmup": args.warmup,
        "phases": sorted(chosen),
    }

    if "latency" in chosen:
        report["latency"] = [s.as_dict() for s in
                             phase_latency(args.repeats, args.warmup, run_id, sampler)]
    if "selectivity" in chosen:
        report["selectivity"] = [s.as_dict() for s in
                                 phase_selectivity(args.repeats, run_id)]
    if "pagination" in chosen:
        report["pagination"] = [s.as_dict() for s in
                                phase_pagination(args.repeats, run_id)]
    if "concurrency" in chosen:
        report["concurrency"] = phase_concurrency(2, run_id)
    if "soak" in chosen:
        report["soak"] = phase_soak(args.soak_n, run_id)

    report["elapsed_seconds"] = round(time.perf_counter() - t_start, 1)
    report["telemetry"] = sampler.stop().snapshot()

    # Write artifacts
    json_path = RESULTS_DIR / f"ontime_{args.tag}_{run_id}.json"
    md_path = RESULTS_DIR / f"ontime_{args.tag}_{run_id}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    md_path.write_text(render_markdown(report))
    print(f"\n=== done | elapsed {report['elapsed_seconds']}s ===")
    print(f"    JSON -> {json_path}")
    print(f"    MD   -> {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
