"""Ingestion quality gate — pre-write quality checks for incoming data."""

from __future__ import annotations

import time
from dataclasses import dataclass

import pyarrow as pa
import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class GateResult:
    """Result of an ingestion quality gate check."""

    total: int
    passed: int
    rejected: int
    schema_rejected: int
    filter_rejected: int
    score_rejected: int
    pass_rate: float
    rejection_reasons: tuple[str, ...]
    duration_seconds: float


class IngestionQualityGate:
    """Quality gate that runs checks BEFORE data is written to Lance.

    Three-stage pipeline:
    1. Schema validation (reuses SchemaValidationGate)
    2. Content filtering (reuses QualityFilterRegistry)
    3. Quality scoring threshold (reuses compute_quality_scores)

    Rejected rows are optionally routed to dead-letter storage.
    """

    def __init__(
        self,
        *,
        schema_mode: str = "lenient",
        active_filters: str = "",
        filter_mode: str = "all",
        min_quality_score: float = 0.0,
        dead_letter_writer: Any | None = None,
        target_schema: pa.Schema | None = None,
    ) -> None:
        self._schema_mode = schema_mode
        self._active_filters = active_filters
        self._filter_mode = filter_mode
        self._min_quality_score = min_quality_score
        self._dead_letter_writer = dead_letter_writer
        self._target_schema = target_schema

    def check(self, table: pa.Table, *, dataset_name: str = "") -> tuple[pa.Table, GateResult]:
        """Run all quality checks on table. Returns (passed_table, result)."""
        from arrow_lake.core.metrics import quality_check_total, quality_reject_total

        start = time.monotonic()
        total_rows = table.num_rows
        schema_rejected = 0
        filter_rejected = 0
        score_rejected = 0
        reasons: list[str] = []
        current = table

        # ── Stage 1: Schema validation ──
        if self._target_schema is not None:
            current, n_schema_rej = self._validate_schema(current)
            schema_rejected = n_schema_rej
            if schema_rejected > 0:
                reasons.append(f"schema:{schema_rejected}")
        elif current.num_rows == 0:
            schema_rejected = total_rows
            reasons.append("schema:empty_table")

        # ── Stage 2: Content filtering ──
        if self._active_filters:
            current, n_filter_rej = self._apply_filters(current, dataset_name)
            filter_rejected = n_filter_rej
            if filter_rejected > 0:
                reasons.append(f"filter:{filter_rejected}")

        # ── Stage 3: Quality scoring ──
        if self._min_quality_score > 0.0 and current.num_rows > 0:
            current, n_score_rej = self._apply_score_threshold(current)
            score_rejected = n_score_rej
            if score_rejected > 0:
                reasons.append(f"score:{score_rejected}")

        # ── Metrics ──
        passed = current.num_rows
        rejected = total_rows - passed
        elapsed = time.monotonic() - start

        if dataset_name:
            quality_check_total.labels(dataset=dataset_name).inc()
        for reason in reasons:
            quality_reject_total.labels(
                dataset=dataset_name or "_unknown", reason=reason
            ).inc(total_rows - passed)

        # ── Dead letter ──
        if rejected > 0 and self._dead_letter_writer is not None:
            self._route_to_dead_letter(table, current, dataset_name)

        result = GateResult(
            total=total_rows,
            passed=passed,
            rejected=rejected,
            schema_rejected=schema_rejected,
            filter_rejected=filter_rejected,
            score_rejected=score_rejected,
            pass_rate=round(passed / max(total_rows, 1), 4),
            rejection_reasons=tuple(reasons),
            duration_seconds=round(elapsed, 4),
        )
        return current, result

    # ── Stage implementations ──

    def _validate_schema(self, table: pa.Table) -> tuple[pa.Table, int]:
        """Run schema validation and return (valid_table, rejected_count)."""
        from arrow_lake.quality.schema_validation import SchemaValidationGate

        gate = SchemaValidationGate(mode=self._schema_mode)
        rows = table.to_pydict()
        row_list = [
            {col: rows[col][i] for col in rows}
            for i in range(table.num_rows)
        ]
        valid, rejected = gate.validate(row_list, self._target_schema)
        if not valid:
            return _dicts_to_table(valid, self._target_schema), len(rejected)
        return table, 0

    def _apply_filters(self, table: pa.Table, dataset_name: str) -> tuple[pa.Table, int]:
        """Run quality filters and return (passed_table, rejected_count)."""
        from arrow_lake.quality.base import QualityFilterRegistry

        registry = QualityFilterRegistry()
        report = registry.apply_all(table, self._active_filters, mode=self._filter_mode)
        if report.passed_count < table.num_rows:
            return report.passed_table, report.rejected_count
        return table, 0

    def _apply_score_threshold(self, table: pa.Table) -> tuple[pa.Table, int]:
        """Filter rows below quality score threshold."""
        from arrow_lake.quality.scoring import compute_quality_scores

        scored = compute_quality_scores(table, _DummyReport())
        scores = scored.column("quality_score")
        mask = [
            v.as_py() is not None and v.as_py() >= self._min_quality_score
            for v in scores
        ]
        filtered = scored.filter(mask)
        if "quality_score" in table.column_names:
            filtered = filtered.drop_columns(["quality_score"])
        return filtered, table.num_rows - filtered.num_rows

    def _route_to_dead_letter(
        self, original: pa.Table, passed: pa.Table, dataset_name: str
    ) -> None:
        """Write rejected rows to dead letter storage."""
        try:
            if passed.num_rows == 0:
                rejected = original
            else:
                passed_ids = set()
                if "id" in original.column_names:
                    passed_ids = {
                        v.as_py() for v in passed.column("id")
                    }
                    rejected_rows = [
                        i for i in range(original.num_rows)
                        if original.column("id")[i].as_py() not in passed_ids
                    ]
                else:
                    return
                if not rejected_rows:
                    return
                rejected = original.take(rejected_rows)

            self._dead_letter_writer.write(dataset_name, rejected, "quality_gate")
        except Exception:
            logger.debug("quality_gate.dead_letter_failed", dataset=dataset_name, exc_info=True)


def _dicts_to_table(rows: list[dict], schema: pa.Schema) -> pa.Table:
    """Convert a list of dicts to a PyArrow Table matching the given schema."""
    if not rows:
        return pa.table({f.name: [] for f in schema})
    cols: dict[str, list] = {f.name: [] for f in schema}
    for row in rows:
        for field in schema:
            cols[field.name].append(row.get(field.name))
    return pa.table(cols, schema=schema)


class _DummyReport:
    """Minimal report stub for compute_quality_scores compatibility."""

    passed_count = 0
    rejected_count = 0
    passed_table: pa.Table | None = None
    rejected_table: pa.Table | None = None


from typing import Any  # noqa: E402 — needed for forward reference
