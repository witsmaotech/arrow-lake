"""Dataset quality profiler — column-level statistics and quality metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc


@dataclass(frozen=True)
class ColumnProfile:
    """Quality profile for a single column."""

    name: str
    dtype: str
    null_count: int
    null_percentage: float
    unique_count: int
    min_value: Any
    max_value: Any
    histogram: tuple[dict[str, Any], ...] | None


@dataclass(frozen=True)
class DatasetQualityProfile:
    """Full quality profile for a dataset."""

    dataset_name: str
    total_rows: int
    total_columns: int
    overall_quality_score: float
    column_profiles: tuple[ColumnProfile, ...]
    profiled_at: str


class QualityProfiler:
    """Computes dataset-level quality statistics.

    Profiles null percentages, value distributions, and quality scores.
    Results can be stored as Lance dataset metadata.
    """

    def profile(self, table: pa.Table, dataset_name: str) -> DatasetQualityProfile:
        """Compute a full quality profile for the given table."""
        column_profiles = tuple(
            self._profile_column(table.column(col_name), col_name)
            for col_name in table.column_names
        )

        overall_score = self._compute_overall_score(table)

        return DatasetQualityProfile(
            dataset_name=dataset_name,
            total_rows=table.num_rows,
            total_columns=table.num_columns,
            overall_quality_score=overall_score,
            column_profiles=column_profiles,
            profiled_at=datetime.now(tz=UTC).isoformat(),
        )

    def _profile_column(self, column: pa.ChunkedArray, name: str) -> ColumnProfile:
        """Compute quality profile for a single column."""
        total = len(column)
        null_count = column.null_count
        null_pct = round(null_count / max(total, 1) * 100, 2)

        dtype = str(column.type)
        unique_count = 0
        min_val: Any = None
        max_val: Any = None
        histogram: tuple[dict[str, Any], ...] | None = None

        non_null = pc.drop_null(column)
        if len(non_null) > 0:
            try:
                unique_count = pc.distinct_count(non_null, mode="all").as_py()
            except Exception:
                unique_count = 0

            try:
                min_max = pc.min_max(non_null)
                min_val = min_max["min"].as_py()
                max_val = min_max["max"].as_py()
            except Exception:
                pass

            if pa.types.is_integer(column.type) or pa.types.is_floating(column.type):
                histogram = self._compute_histogram(non_null, max_bins=10)

        return ColumnProfile(
            name=name,
            dtype=dtype,
            null_count=null_count,
            null_percentage=null_pct,
            unique_count=unique_count,
            min_value=min_val,
            max_value=max_val,
            histogram=histogram,
        )

    def _compute_histogram(
        self, column: pa.ChunkedArray, max_bins: int = 10
    ) -> tuple[dict[str, Any], ...]:
        """Compute a simple histogram for numeric columns."""
        try:
            values = column.to_numpy()
            import numpy as np

            counts, edges = np.histogram(values, bins=max_bins)
            bins: list[dict[str, Any]] = []
            for i in range(len(counts)):
                bins.append({
                    "lower": float(edges[i]),
                    "upper": float(edges[i + 1]),
                    "count": int(counts[i]),
                })
            return tuple(bins)
        except Exception:
            return ()

    def _compute_overall_score(self, table: pa.Table) -> float:
        """Compute overall quality score based on null percentages."""
        if table.num_rows == 0 or table.num_columns == 0:
            return 0.0
        if "quality_score" in table.column_names:
            scores = table.column("quality_score")
            non_null = pc.drop_null(scores)
            if len(non_null) > 0:
                return round(float(pc.mean(non_null).as_py()), 4)

        total_cells = table.num_rows * table.num_columns
        null_cells = sum(table.column(c).null_count for c in table.column_names)
        return round(1.0 - (null_cells / max(total_cells, 1)), 4)
