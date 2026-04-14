"""Structured benchmark report generator — Story 5.8."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Measurement:
    """A single benchmark measurement."""

    label: str
    elapsed_seconds: float
    throughput: float | None = None
    rows: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class BenchmarkReport:
    """Collects and serializes benchmark measurements as JSON.

    Usage::

        report = BenchmarkReport("vector_search")
        elapsed = report.measure("10k rows", fn=lambda: search(...), rows=10000)
        print(report.to_json())
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._measurements: list[Measurement] = []

    def measure(
        self,
        label: str,
        fn: Any,
        *,
        rows: int | None = None,
        warmup: int = 1,
        repeats: int = 5,
    ) -> float:
        """Time a function call and record the result.

        Args:
            label: Description of the benchmark.
            fn: Callable to benchmark.
            rows: Number of rows processed (for throughput calc).
            warmup: Number of warmup iterations (not recorded).
            repeats: Number of timed iterations (median is recorded).

        Returns:
            Median elapsed time in seconds.
        """
        # Warmup
        for _ in range(warmup):
            fn()

        timings: list[float] = []
        for _ in range(repeats):
            start = time.perf_counter()
            fn()
            elapsed = time.perf_counter() - start
            timings.append(elapsed)

        timings.sort()
        median = timings[len(timings) // 2]
        throughput = (rows / median) if rows and median > 0 else None

        measurement = Measurement(
            label=label,
            elapsed_seconds=median,
            throughput=throughput,
            rows=rows,
        )
        self._measurements.append(measurement)
        return median

    def add(self, measurement: Measurement) -> None:
        """Add a pre-computed measurement."""
        self._measurements.append(measurement)

    def to_json(self) -> str:
        """Serialize report to JSON."""
        data = {
            "benchmark": self.name,
            "measurements": [
                {
                    "label": m.label,
                    "elapsed_seconds": round(m.elapsed_seconds, 6),
                    "throughput": round(m.throughput, 1) if m.throughput else None,
                    "rows": m.rows,
                    **m.extra,
                }
                for m in self._measurements
            ],
        }
        return json.dumps(data, indent=2)

    def print_summary(self) -> None:
        """Print a human-readable summary to stdout."""
        print(f"\n[BENCHMARK] {self.name}")
        for m in self._measurements:
            line = f"  {m.label}: {m.elapsed_seconds:.4f}s"
            if m.throughput:
                line += f" ({m.throughput:,.0f} rows/s)"
            if m.rows:
                line += f" [{m.rows:,} rows]"
            print(line)
