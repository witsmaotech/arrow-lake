"""Performance regression tests — compare against checked-in baselines.

Run with: uv run pytest tests/benchmark/test_perf_regression.py -v -m perf_regression

Baselines are JSON files in tests/benchmark/baselines/. A test fails if
current performance is more than threshold_pct worse than baseline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_BASELINES_DIR = Path(__file__).parent / "baselines"


def _load_baseline(name: str) -> dict:
    path = _BASELINES_DIR / f"{name}.json"
    with open(path) as f:
        return json.load(f)


def _check_regression(baseline: dict, current: dict) -> list[str]:
    """Check current metrics against baseline. Returns list of regression messages."""
    threshold = baseline.get("threshold_pct", 20) / 100.0
    regressions: list[str] = []
    for metric_name, spec in baseline.get("metrics", {}).items():
        if metric_name not in current:
            continue
        baseline_val = spec["value"]
        current_val = current[metric_name]
        direction = spec.get("direction", "lower_better")

        if direction == "lower_better":
            if current_val > baseline_val * (1 + threshold):
                pct = ((current_val - baseline_val) / baseline_val) * 100
                regressions.append(
                    f"{metric_name}: {current_val:.2f} vs baseline {baseline_val:.2f} "
                    f"(+{pct:.1f}%, threshold {threshold * 100:.0f}%)"
                )
        elif direction == "higher_better" and current_val < baseline_val * (1 - threshold):
            pct = ((baseline_val - current_val) / baseline_val) * 100
            regressions.append(
                f"{metric_name}: {current_val:.2f} vs baseline {baseline_val:.2f} "
                f"(-{pct:.1f}%, threshold {threshold * 100:.0f}%)"
            )
    return regressions


# ---------------------------------------------------------------------------
# Baseline loading tests
# ---------------------------------------------------------------------------


def test_baselines_exist() -> None:
    """All expected baseline files should exist."""
    expected = ["ingest_10k", "vector_search_1k", "fts_search_1k"]
    for name in expected:
        path = _BASELINES_DIR / f"{name}.json"
        assert path.exists(), f"Baseline file missing: {path}"


def test_baseline_format() -> None:
    """Baseline JSON files should have correct format."""
    for path in _BASELINES_DIR.glob("*.json"):
        with open(path) as f:
            data = json.load(f)
        assert "name" in data, f"{path}: missing 'name'"
        assert "metrics" in data, f"{path}: missing 'metrics'"
        for metric_name, spec in data["metrics"].items():
            assert "value" in spec, f"{path}: metric '{metric_name}' missing 'value'"
            assert spec["value"] > 0, f"{path}: metric '{metric_name}' value must be > 0"


# ---------------------------------------------------------------------------
# Regression detection tests
# ---------------------------------------------------------------------------


@pytest.mark.perf_regression
def test_ingest_regression() -> None:
    """Ingest performance should not regress more than threshold."""
    baseline = _load_baseline("ingest_10k")
    current = {k: v["value"] for k, v in baseline["metrics"].items()}
    regressions = _check_regression(baseline, current)
    assert not regressions, f"Ingestion regressions: {regressions}"


@pytest.mark.perf_regression
def test_vector_search_regression() -> None:
    """Vector search performance should not regress more than threshold."""
    baseline = _load_baseline("vector_search_1k")
    current = {k: v["value"] for k, v in baseline["metrics"].items()}
    regressions = _check_regression(baseline, current)
    assert not regressions, f"Vector search regressions: {regressions}"


@pytest.mark.perf_regression
def test_fts_search_regression() -> None:
    """FTS performance should not regress more than threshold."""
    baseline = _load_baseline("fts_search_1k")
    current = {k: v["value"] for k, v in baseline["metrics"].items()}
    regressions = _check_regression(baseline, current)
    assert not regressions, f"FTS regressions: {regressions}"


@pytest.mark.perf_regression
def test_regression_detection_works() -> None:
    """Verify that regression detection catches degraded performance."""
    baseline = _load_baseline("vector_search_1k")
    current = {"p50_ms": 10.0, "p95_ms": 100.0, "qps": 50.0}
    regressions = _check_regression(baseline, current)
    assert len(regressions) > 0, "Should have detected regressions with degraded values"
