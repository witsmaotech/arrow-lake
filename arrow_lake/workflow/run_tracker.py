"""Run tracker — Metaflow Client API wrapper for run history and comparison.

Provides structured access to past flow runs without coupling
callers to Metaflow's Client API internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunSummary:
    """Summary of a single flow run."""

    run_id: str
    status: str
    created_at: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class RunComparison:
    """Comparison between two runs of the same flow."""

    run_a_id: str
    run_b_id: str
    metrics_a: dict[str, Any]
    metrics_b: dict[str, Any]
    diff: dict[str, float]


class RunTracker:
    """Query and compare Metaflow flow run history.

    All methods are static and safe to call without instantiation.
    Falls back gracefully when Metaflow metadata service is unavailable.
    """

    @staticmethod
    def latest_run(flow_name: str) -> RunSummary | None:
        """Get the most recent successful run for a flow.

        Args:
            flow_name: Registered flow name (e.g. "IngestFlow").

        Returns:
            RunSummary or None if no successful runs exist.
        """
        from metaflow import Flow

        for run in Flow(flow_name):
            if run.successful:
                return RunSummary(
                    run_id=str(run.id),
                    status="success",
                    created_at=str(run.created_at),
                    tags=tuple(run.tags),
                )
        return None

    @staticmethod
    def run_history(flow_name: str, limit: int = 10) -> list[RunSummary]:
        """Get recent run history for a flow.

        Args:
            flow_name: Registered flow name.
            limit: Maximum number of runs to return.

        Returns:
            List of RunSummary objects, newest first.
        """
        from metaflow import Flow

        history: list[RunSummary] = []
        for i, run in enumerate(Flow(flow_name)):
            if i >= limit:
                break
            history.append(
                RunSummary(
                    run_id=str(run.id),
                    status="success" if run.successful else "failed",
                    created_at=str(run.created_at),
                    tags=tuple(run.tags),
                )
            )
        return history

    @staticmethod
    def compare_runs(
        flow_name: str, run_a_id: str, run_b_id: str
    ) -> RunComparison:
        """Compare metrics between two runs.

        Args:
            flow_name: Registered flow name.
            run_a_id: First run ID.
            run_b_id: Second run ID.

        Returns:
            RunComparison with metrics and diff.
        """
        from metaflow import Run

        def _extract(run: Any) -> dict[str, Any]:
            return {
                "total_rows": getattr(run.data, "total_rows", 0),
                "success_count": getattr(run.data, "total_success", 0),
                "failure_count": getattr(run.data, "total_failure", 0),
            }

        run_a = Run(f"{flow_name}/{run_a_id}")
        run_b = Run(f"{flow_name}/{run_b_id}")

        metrics_a = _extract(run_a)
        metrics_b = _extract(run_b)

        diff = {
            k: float(metrics_b.get(k, 0) - metrics_a.get(k, 0))
            for k in set(metrics_a) | set(metrics_b)
        }

        return RunComparison(
            run_a_id=run_a_id,
            run_b_id=run_b_id,
            metrics_a=metrics_a,
            metrics_b=metrics_b,
            diff=diff,
        )
