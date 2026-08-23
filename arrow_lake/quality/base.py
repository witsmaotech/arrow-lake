"""QualityFilter Protocol and Registry (Story 4.8).

Provides the pluggable filter interface and a registry that orchestrates
AND/OR filter combinations over PyArrow tables.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

import pyarrow as pa
import structlog

from arrow_lake.quality.models import FilterResult, QualityReport

logger = structlog.get_logger(__name__)


@runtime_checkable
class QualityFilter(Protocol):
    """Protocol for quality filters.

    Implementations must provide a read-only ``name`` property and a
    ``filter`` method that splits an input table into passed/rejected.
    """

    @property
    def name(self) -> str: ...

    def filter(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
        """Split *table* into (passed, rejected).

        Returns:
            Two-table tuple.  Both may be empty but never ``None``.
        """
        ...


class QualityFilterRegistry:
    """Registry for named quality filters with AND/OR apply modes.

    AND (``"all"``):  each filter sees only the rows that survived the
    previous filter (cumulative reject).  Short-circuits when no rows remain.

    OR (``"any"``):  each filter sees only the rows that *failed* the
    previous filter (cumulative pass).  Short-circuits when all rows pass.
    """

    def __init__(self) -> None:
        self._filters: dict[str, QualityFilter] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, filt: QualityFilter) -> None:
        """Register a filter by its ``name``."""
        if filt.name in self._filters:
            raise ValueError(f"Filter '{filt.name}' is already registered")
        self._filters[filt.name] = filt
        logger.debug("quality_filter_registered", name=filt.name)

    def unregister(self, name: str) -> None:
        """Remove a registered filter."""
        if name not in self._filters:
            raise KeyError(f"Filter '{name}' is not registered")
        del self._filters[name]
        logger.debug("quality_filter_unregistered", name=name)

    def get(self, name: str) -> QualityFilter:
        """Retrieve a registered filter by name."""
        if name not in self._filters:
            raise KeyError(f"Filter '{name}' is not registered")
        return self._filters[name]

    def list_filters(self) -> list[str]:
        """Return sorted list of registered filter names."""
        return sorted(self._filters.keys())

    def clear(self) -> None:
        """Remove all registered filters."""
        self._filters.clear()

    # ------------------------------------------------------------------
    # Active filter selection
    # ------------------------------------------------------------------

    def get_active_filters(
        self,
        active_filters: str,
        *,
        skip_unknown: bool = True,
    ) -> list[QualityFilter]:
        """Resolve a comma-separated filter name list to filter instances.

        Args:
            active_filters: Comma-separated filter names, or empty string.
            skip_unknown: If True, silently skip unregistered names.
                If False, raise KeyError for unknown names.

        Returns:
            List of QualityFilter instances in requested order.
        """
        if not active_filters.strip():
            return []
        names = [n.strip() for n in active_filters.split(",") if n.strip()]
        result: list[QualityFilter] = []
        for name in names:
            if name not in self._filters:
                if skip_unknown:
                    logger.warning("quality_filter_unknown_skipped", name=name)
                    continue
                raise KeyError(f"Filter '{name}' is not registered")
            result.append(self._filters[name])
        return result

    # ------------------------------------------------------------------
    # Batch apply
    # ------------------------------------------------------------------

    def apply_all(
        self,
        table: pa.Table,
        active_filters: str,
        *,
        mode: str = "all",
    ) -> QualityReport:
        """Apply active filters to *table* and return a QualityReport.

        Args:
            table: Input PyArrow table.
            active_filters: Comma-separated filter names.
            mode: ``"all"`` (AND) or ``"any"`` (OR).

        Returns:
            QualityReport with per-filter results and totals.
        """
        t0 = time.monotonic()
        filters = self.get_active_filters(active_filters)

        if not filters or table.num_rows == 0:
            return QualityReport(
                total=table.num_rows,
                passed=table.num_rows,
                rejected=0,
                duration_seconds=time.monotonic() - t0,
            )

        filter_results: list[FilterResult] = []

        if mode == "any":
            passed, rejected, filter_results = self._apply_or(table, filters)
        else:
            passed, rejected, filter_results = self._apply_and(table, filters)

        return QualityReport(
            total=table.num_rows,
            passed=passed.num_rows,
            rejected=rejected.num_rows,
            filter_results=tuple(filter_results),
            duration_seconds=time.monotonic() - t0,
            # v1.10.7 WP5: row-level tables for exact dead-letter routing.
            passed_table=passed,
            rejected_table=rejected,
        )

    # ------------------------------------------------------------------
    # Internal AND / OR strategies
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_and(
        table: pa.Table,
        filters: list[QualityFilter],
    ) -> tuple[pa.Table, pa.Table, list[FilterResult]]:
        """AND: each filter sees only surviving (passing) rows."""
        accumulated_rejected_chunks: list[pa.Table] = []
        current = table
        results: list[FilterResult] = []

        for filt in filters:
            passed, rejected = filt.filter(current)
            results.append(
                FilterResult(
                    filter_name=filt.name,
                    passed_count=passed.num_rows,
                    rejected_count=rejected.num_rows,
                )
            )
            if rejected.num_rows > 0:
                accumulated_rejected_chunks.append(rejected)
            current = passed
            if current.num_rows == 0:
                break

        if accumulated_rejected_chunks:
            base_cols = [
                c for c in table.column_names if c != "_rejection_reason"
            ]
            normalized = []
            for chunk in accumulated_rejected_chunks:
                chunk_cols = [
                    c for c in chunk.column_names if c in base_cols
                ]
                normalized.append(chunk.select(chunk_cols))
            all_rejected = pa.concat_tables(normalized)
        else:
            all_rejected = table.slice(0, 0)
        return current, all_rejected, results

    @staticmethod
    def _apply_or(
        table: pa.Table,
        filters: list[QualityFilter],
    ) -> tuple[pa.Table, pa.Table, list[FilterResult]]:
        """OR: each filter sees only the (still-failing) rejected rows."""
        accumulated_passed_chunks: list[pa.Table] = []
        current = table
        results: list[FilterResult] = []

        for filt in filters:
            passed, rejected = filt.filter(current)
            results.append(
                FilterResult(
                    filter_name=filt.name,
                    passed_count=passed.num_rows,
                    rejected_count=rejected.num_rows,
                )
            )
            if passed.num_rows > 0:
                accumulated_passed_chunks.append(passed)
            current = rejected
            if current.num_rows == 0:
                break

        if accumulated_passed_chunks:
            base_cols = [
                c for c in table.column_names if c != "_rejection_reason"
            ]
            normalized = []
            for chunk in accumulated_passed_chunks:
                chunk_cols = [
                    c for c in chunk.column_names if c in base_cols
                ]
                normalized.append(chunk.select(chunk_cols))
            all_passed = pa.concat_tables(normalized)
        else:
            all_passed = table.slice(0, 0)
        return all_passed, current, results
