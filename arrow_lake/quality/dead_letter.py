"""Dead-letter persistence (Story 4.10).

Writes rejected rows to a ``{table_name}_dead_letter`` Lance table
with extra metadata columns tracking why and when each row was rejected.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import pyarrow as pa
import structlog

from arrow_lake.exceptions import ErrorCode, QualityError
from arrow_lake.quality.models import DEAD_LETTER_EXTRA_SCHEMA

logger = structlog.get_logger(__name__)


@runtime_checkable
class StorageWriter(Protocol):
    """Minimal protocol for a Lance storage backend."""

    def write(self, table_name: str, table: pa.Table) -> int: ...


class DeadLetterWriter:
    """Persists quality-rejected rows to a dead-letter Lance table.

    Args:
        storage: A storage backend implementing the ``StorageWriter`` protocol.
    """

    def __init__(self, storage: StorageWriter) -> None:
        self._storage = storage

    def write(
        self,
        table_name: str,
        rejected: pa.Table,
        filter_name: str,
        *,
        parent_version: str = "",
    ) -> int:
        """Write rejected rows to the dead-letter table.

        Args:
            table_name: Original dataset name (dead-letter gets ``_dead_letter`` suffix).
            rejected: Table of rejected rows (may include ``_rejection_reason`` column).
            filter_name: Name of the filter that rejected these rows.
            parent_version: Optional Lance version tag of the parent table.

        Returns:
            Number of rows written.

        Raises:
            QualityError: If the storage write fails.
        """
        if rejected.num_rows == 0:
            logger.debug("dead_letter_write_skipped", reason="empty_table")
            return 0

        dead_letter_table = table_name + "_dead_letter"
        rejected_at = datetime.now(UTC).isoformat()

        # Extract existing rejection reason if present, otherwise use filter_name
        if "_rejection_reason" in rejected.column_names:
            rejection_reasons = rejected.column("_rejection_reason")
            rejected = rejected.drop_columns(["_rejection_reason"])
        else:
            rejection_reasons = pa.array(
                [f"Rejected by {filter_name}"] * rejected.num_rows,
                type=pa.string(),
            )

        extra_cols = [
            rejection_reasons,
            pa.array([filter_name] * rejected.num_rows, type=pa.string()),
            pa.array([parent_version or None] * rejected.num_rows, type=pa.string()),
            pa.array([rejected_at] * rejected.num_rows, type=pa.string()),
        ]

        for i, field in enumerate(DEAD_LETTER_EXTRA_SCHEMA):
            rejected = rejected.append_column(field.name, extra_cols[i])

        try:
            written = self._storage.write(dead_letter_table, rejected)
            logger.info(
                "dead_letter_written",
                table=dead_letter_table,
                rows=written,
                filter=filter_name,
            )
            return written
        except OSError as exc:
            raise QualityError(
                error_code=ErrorCode.QUALITY_DEAD_LETTER_WRITE_FAILED,
                message=f"Failed to write dead-letter table '{dead_letter_table}': {exc}",
                context={"table_name": dead_letter_table, "filter": filter_name},
            ) from exc
