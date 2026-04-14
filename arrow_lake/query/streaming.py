"""Streaming result iterator — Story 5.5.

Provides memory-efficient iteration over large query results
by yielding batches of rows instead of materializing the full table.
"""

from __future__ import annotations

from collections.abc import Iterator

import pyarrow as pa


class StreamingResult:
    """Iterates over an Arrow table in fixed-size batches.

    Useful for large result sets where materializing the full table
    at once would exceed memory limits.

    Args:
        table: Source Arrow table to stream.
        batch_size: Number of rows per batch.
    """

    def __init__(self, table: pa.Table, batch_size: int = 1000) -> None:
        self._table = table
        self._batch_size = max(1, batch_size)
        self._offset = 0

    @property
    def total_rows(self) -> int:
        """Total number of rows in the source table."""
        return self._table.num_rows

    @property
    def remaining_rows(self) -> int:
        """Number of rows not yet yielded."""
        return max(0, self._table.num_rows - self._offset)

    @property
    def is_exhausted(self) -> bool:
        """Whether all rows have been yielded."""
        return self._offset >= self._table.num_rows

    def __iter__(self) -> Iterator[pa.Table]:
        return self

    def __next__(self) -> pa.Table:
        if self._offset >= self._table.num_rows:
            raise StopIteration
        end = min(self._offset + self._batch_size, self._table.num_rows)
        batch = self._table.slice(self._offset, end - self._offset)
        self._offset = end
        return batch

    def collect(self) -> pa.Table:
        """Collect all remaining rows into a single table."""
        if self._offset >= self._table.num_rows:
            return self._table.slice(0, 0)
        remaining = self._table.slice(self._offset)
        self._offset = self._table.num_rows
        return remaining

    @property
    def columns(self) -> list[str]:
        """Column names in the source table."""
        return self._table.column_names

    @property
    def schema(self) -> pa.Schema:
        """Schema of the source table."""
        return self._table.schema
