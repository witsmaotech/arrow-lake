"""Quality reporting models and dead-letter schema constants (Epic 4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyarrow as pa


@dataclass(frozen=True)
class FilterResult:
    """Result of a single quality filter execution."""

    filter_name: str
    passed_count: int
    rejected_count: int

    def pass_rate(self) -> float:
        """Return pass rate as a percentage (0.0–100.0)."""
        total = self.passed_count + self.rejected_count
        if total == 0:
            return 100.0
        return (self.passed_count / total) * 100.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return {
            "filter_name": self.filter_name,
            "passed_count": self.passed_count,
            "rejected_count": self.rejected_count,
            "pass_rate_percentage": round(self.pass_rate(), 1),
        }


@dataclass(frozen=True)
class QualityReport:
    """Aggregate report for a quality filtering pass.

    Attributes:
        total: Total rows input to the quality pipeline.
        passed: Rows that passed all filters.
        rejected: Rows rejected by at least one filter.
        filter_results: Per-filter breakdown of pass/reject counts.
        schema_rejected: Rows rejected by schema validation (before filters).
        duration_seconds: Wall-clock time of the entire quality pass.
    """

    total: int = 0
    passed: int = 0
    rejected: int = 0
    filter_results: tuple[FilterResult, ...] = ()
    schema_rejected: int = 0
    duration_seconds: float = 0.0

    def overall_pass_rate(self) -> float:
        """Return overall pass rate as a percentage."""
        if self.total == 0:
            return 100.0
        return (self.passed / self.total) * 100.0

    def to_json(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary.

        Compatible with Metaflow Cards for visualization.
        """
        return {
            "total_rows": self.total,
            "passed_rows": self.passed,
            "rejected_rows": self.rejected,
            "schema_rejected_rows": self.schema_rejected,
            "overall_pass_rate_percentage": round(self.overall_pass_rate(), 1),
            "duration_seconds": round(self.duration_seconds, 4),
            "per_filter": [f.to_dict() for f in self.filter_results],
        }

    def per_filter_breakdown(self) -> list[dict[str, Any]]:
        """Return per-filter breakdown as list of dicts."""
        return [f.to_dict() for f in self.filter_results]


#: Extra columns appended to dead-letter tables.
#: These track *why* and *when* a row was rejected.
DEAD_LETTER_EXTRA_SCHEMA = pa.schema(
    [
        pa.field("_rejection_reason", pa.string(), nullable=False),
        pa.field("_filter_name", pa.string(), nullable=False),
        pa.field("_parent_version", pa.string(), nullable=True),
        pa.field("_rejected_at", pa.string(), nullable=False),
    ]
)
