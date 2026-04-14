"""Quality score computation — Story 4.13.

Computes a composite quality score (0.0–1.0) for each row based on
filter pass/reject results and appends it as a Lance column.
"""

from __future__ import annotations

import pyarrow as pa

from arrow_lake.quality.models import QualityReport


def compute_quality_scores(
    table: pa.Table,
    report: QualityReport,
    *,
    rejected_table: pa.Table | None = None,
    score_column: str = "quality_score",
) -> pa.Table:
    """Append a quality score column to the table.

    Score logic:
    - Rows that passed all filters: 1.0
    - Rows rejected by N filters: max(0.0, 1.0 - 0.2 * N)
      — each filter rejection reduces score by 0.2, min 0.0

    Args:
        table: Arrow table to annotate.
        report: QualityReport from the filtering pass.
        rejected_table: Table of rejected rows (used to build per-row mask).
            When None, the first ``report.rejected`` rows are assumed rejected.
        score_column: Name of the score column to add.

    Returns:
        New table with the score column appended.

    Raises:
        ValueError: If ``report.total`` does not match ``table.num_rows``.
    """
    if report.total != table.num_rows:
        raise ValueError(
            f"Report total ({report.total}) does not match table rows ({table.num_rows})"
        )

    total = report.total
    if total == 0:
        scores = pa.array([], type=pa.float32())
        return table.append_column(score_column, scores)

    rejected = report.rejected
    n_filters = len(report.filter_results) or 1
    penalty = min(1.0, 0.2 * n_filters)

    # Build per-row rejection mask.
    if rejected_table is not None and rejected_table.num_rows > 0:
        # Use first column as identity key for set-membership lookup.
        id_col = table.column(0)
        rejected_id_col = rejected_table.column(0)

        # Build a Python set for O(1) lookup.
        rejected_ids = set(rejected_id_col.to_pylist())
        id_values = id_col.to_pylist()

        scores_list: list[float] = [
            max(0.0, 1.0 - penalty) if val in rejected_ids else 1.0 for val in id_values
        ]
    else:
        # Fallback: first ``rejected`` rows are penalized.
        scores_list = [max(0.0, 1.0 - penalty) if i < rejected else 1.0 for i in range(total)]

    scores = pa.array(scores_list, type=pa.float32())
    return table.append_column(score_column, scores)
