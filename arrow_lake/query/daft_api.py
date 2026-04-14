"""Daft DataFrame API — Story 3.7.

Provides lazy DataFrame operations via Daft for Lance datasets.
All operations are lazy until .collect() is called.
"""

from __future__ import annotations

import re
from pathlib import Path

import daft
import pyarrow as pa

_SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")


class LazyDaftFrame:
    """Lazy DataFrame wrapper around Daft.

    All operations return a new LazyDaftFrame without executing.
    Call .collect() to materialize results as an Arrow Table.
    """

    def __init__(self, df: daft.DataFrame) -> None:
        self._df = df

    def select(self, *columns: str) -> LazyDaftFrame:
        """Select specific columns.

        Args:
            columns: Column names to select.

        Returns:
            New LazyDaftFrame with selected columns.

        Raises:
            ValueError: If any column name is not a safe identifier.
        """
        for col in columns:
            if not _SAFE_IDENTIFIER_RE.match(col):
                raise ValueError(f"Invalid column name '{col}'")
        return LazyDaftFrame(self._df.select(*columns))

    def filter(self, predicate: str) -> LazyDaftFrame:
        """Filter rows by a predicate expression.

        Args:
            predicate: Daft expression string (e.g. "age > 30").

        Returns:
            New LazyDaftFrame with filtered rows.
        """
        return LazyDaftFrame(self._df.filter(predicate))

    def sort(self, column: str, desc: bool = False) -> LazyDaftFrame:
        """Sort by a column.

        Args:
            column: Column name to sort by.
            desc: Sort descending if True.

        Returns:
            New LazyDaftFrame with sorted rows.

        Raises:
            ValueError: If column name is not a safe identifier.
        """
        if not _SAFE_IDENTIFIER_RE.match(column):
            raise ValueError(f"Invalid column name '{column}'")
        return LazyDaftFrame(self._df.sort(column, desc=desc))

    def join(
        self,
        other: LazyDaftFrame,
        on: str,
        how: str = "inner",
    ) -> LazyDaftFrame:
        """Join with another frame.

        Args:
            other: Other LazyDaftFrame to join.
            on: Column name to join on.
            how: Join type ("inner", "left", "outer").

        Returns:
            New LazyDaftFrame with joined data.

        Raises:
            ValueError: If join column name is not a safe identifier.
        """
        if not _SAFE_IDENTIFIER_RE.match(on):
            raise ValueError(f"Invalid column name '{on}'")
        if how not in ("inner", "left", "outer"):
            raise ValueError(f"Invalid join type '{how}'")
        return LazyDaftFrame(self._df.join(other._df, on=on, how=how))  # type: ignore[arg-type]

    def groupby(self, *columns: str) -> LazyGroupedFrame:
        """Group by columns.

        Args:
            columns: Column names to group by.

        Returns:
            LazyGroupedFrame for aggregation.

        Raises:
            ValueError: If any column name is not a safe identifier.
        """
        for col in columns:
            if not _SAFE_IDENTIFIER_RE.match(col):
                raise ValueError(f"Invalid column name '{col}'")
        return LazyGroupedFrame(self._df.groupby(*columns))

    def collect(self) -> pa.Table:
        """Execute all lazy operations and return Arrow Table.

        Returns:
            Materialized Arrow Table.
        """
        return self._df.to_arrow()


class LazyGroupedFrame:
    """Lazy grouped DataFrame for aggregation operations."""

    def __init__(self, grouped: daft.dataframe.GroupedDataFrame) -> None:
        self._grouped = grouped

    def collect(self) -> pa.Table:
        """Materialize grouped results.

        Returns:
            Arrow Table with grouped results.
        """
        return self._grouped.to_arrow()  # type: ignore[attr-defined]


class DaftQueryEngine:
    """Query engine using Daft for Lance datasets.

    Provides lazy DataFrame API for efficient data processing.
    All operations are lazy until .collect() is called.

    Args:
        base_uri: Base URI for Lance dataset storage.
    """

    def __init__(self, base_uri: str | Path) -> None:
        self.base_uri = str(base_uri)

    def load(
        self,
        dataset_name: str,
        columns: list[str] | None = None,
    ) -> LazyDaftFrame:
        """Load a Lance dataset as a lazy Daft DataFrame.

        Args:
            dataset_name: Name of the dataset.
            columns: Optional column subset to load.

        Returns:
            LazyDaftFrame for further lazy operations.

        Raises:
            ValueError: If dataset name or column names are not safe identifiers.
        """
        if not _SAFE_IDENTIFIER_RE.match(dataset_name):
            raise ValueError(f"Invalid dataset name '{dataset_name}'")
        if columns:
            for col in columns:
                if not _SAFE_IDENTIFIER_RE.match(col):
                    raise ValueError(f"Invalid column name '{col}'")

        lance_path = str(Path(self.base_uri) / f"{dataset_name}.lance")
        df = daft.read_lance(lance_path)
        if columns:
            df = df.select(*columns)
        return LazyDaftFrame(df)
