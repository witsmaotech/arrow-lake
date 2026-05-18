"""Daft DataFrame API — Story 3.7.

Provides lazy DataFrame operations via Daft for Lance datasets.
All operations are lazy until .collect() is called.
"""

from __future__ import annotations

import inspect
import logging
import re
from pathlib import Path
from typing import Any

import daft
import pyarrow as pa

logger = logging.getLogger(__name__)

__all__ = [
    "DaftQueryEngine",
    "LazyDaftFrame",
    "LazyGroupedFrame",
]

_SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
_PIVOT_AGG_FNS = frozenset({"sum", "mean", "count", "min", "max", "first", "last"})
_SQL_DANGEROUS_RE = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|EXEC)\b",
    re.IGNORECASE,
)
_MAX_SQL_LENGTH = 10_000
_DEFAULT_COLLECT_MAX_ROWS = 100_000
_ROW_COUNT_WARN_THRESHOLD = 500_000
_ROW_COUNT_HARD_LIMIT = 1_000_000


class LazyDaftFrame:
    """Lazy DataFrame wrapper around Daft.

    All operations return a new LazyDaftFrame without executing.
    Call .collect() to materialize results as an Arrow Table.
    """

    def __init__(self, df: daft.DataFrame) -> None:
        self._df = df

    def __repr__(self) -> str:
        cols = self._df.column_names
        return f"LazyDaftFrame(columns={cols})"

    # ── Selection & Projection ──

    def select(self, *columns: str) -> LazyDaftFrame:
        """Select specific columns.

        Raises:
            ValueError: If any column name is not a safe identifier.
        """
        for col in columns:
            if not _SAFE_IDENTIFIER_RE.match(col):
                raise ValueError(f"Invalid column name '{col}'")
        return LazyDaftFrame(self._df.select(*columns))

    def with_column(self, name: str, expr: daft.Expression) -> LazyDaftFrame:
        """Add or replace a column with a computed expression.

        Args:
            name: New column name.
            expr: Daft expression for the column value.

        Returns:
            LazyDaftFrame with the new/replaced column.
        """
        if not _SAFE_IDENTIFIER_RE.match(name):
            raise ValueError(f"Invalid column name '{name}'")
        return LazyDaftFrame(self._df.with_column(name, expr))

    def with_columns(self, columns: dict[str, daft.Expression]) -> LazyDaftFrame:
        """Add or replace multiple columns.

        Args:
            columns: Dict of {name: expression}.

        Returns:
            LazyDaftFrame with new/replaced columns.
        """
        for name in columns:
            if not _SAFE_IDENTIFIER_RE.match(name):
                raise ValueError(f"Invalid column name '{name}'")
        return LazyDaftFrame(self._df.with_columns(columns))

    def exclude(self, *columns: str) -> LazyDaftFrame:
        """Drop columns by name.

        Args:
            columns: Column names to exclude.

        Returns:
            LazyDaftFrame without specified columns.

        Raises:
            ValueError: If any column name is not a safe identifier.
        """
        for col in columns:
            if not _SAFE_IDENTIFIER_RE.match(col):
                raise ValueError(f"Invalid column name '{col}'")
        return LazyDaftFrame(self._df.exclude(*columns))

    # ── Filtering ──

    def filter(self, predicate: daft.Expression) -> LazyDaftFrame:
        """Filter rows by a Daft expression.

        Args:
            predicate: Daft expression, e.g. ``daft.col("age") > 30``.

        Returns:
            LazyDaftFrame with filtered rows.
        """
        return LazyDaftFrame(self._df.filter(predicate))

    def drop_null(self, *columns: str) -> LazyDaftFrame:
        """Drop rows containing null values in specified columns.

        Args:
            columns: Columns to check. Empty = all columns.

        Returns:
            LazyDaftFrame with null rows removed.

        Raises:
            ValueError: If any column name is not a safe identifier.
        """
        for col in columns:
            if not _SAFE_IDENTIFIER_RE.match(col):
                raise ValueError(f"Invalid column name '{col}'")
        if columns:
            return LazyDaftFrame(self._df.drop_null(*columns))
        return LazyDaftFrame(self._df.drop_null())

    def fill_null(self, value: Any, column: str | None = None) -> LazyDaftFrame:
        """Fill null values.

        Args:
            value: Fill value or Daft fill_null expression.
            column: Optional column name. None = all columns.

        Returns:
            LazyDaftFrame with nulls filled.
        """
        if column is not None:
            if not _SAFE_IDENTIFIER_RE.match(column):
                raise ValueError(f"Invalid column name '{column}'")
            return LazyDaftFrame(
                self._df.with_column(column, daft.col(column).fill_null(value))
            )
        return LazyDaftFrame(self._df.with_columns(
            {c: daft.col(c).fill_null(value) for c in self._df.column_names}
        ))

    # ── Sorting & Pagination ──

    def sort(self, column: str, desc: bool = False) -> LazyDaftFrame:
        """Sort by a column.

        Raises:
            ValueError: If column name is not a safe identifier.
        """
        if not _SAFE_IDENTIFIER_RE.match(column):
            raise ValueError(f"Invalid column name '{column}'")
        return LazyDaftFrame(self._df.sort(column, desc=desc))

    def limit(self, n: int) -> LazyDaftFrame:
        """Limit the number of rows.

        Raises:
            ValueError: If n is not a positive integer.
        """
        if n < 1:
            raise ValueError(f"limit must be >= 1, got {n}")
        return LazyDaftFrame(self._df.limit(n))

    def offset(self, n: int) -> LazyDaftFrame:
        """Skip the first N rows (for pagination).

        Raises:
            ValueError: If n is negative.
        """
        if n < 0:
            raise ValueError(f"offset must be >= 0, got {n}")
        return LazyDaftFrame(self._df.offset(n))

    # ── Aggregation ──

    def groupby(self, *columns: str) -> LazyGroupedFrame:
        """Group by columns.

        Raises:
            ValueError: If any column name is not a safe identifier.
        """
        for col in columns:
            if not _SAFE_IDENTIFIER_RE.match(col):
                raise ValueError(f"Invalid column name '{col}'")
        return LazyGroupedFrame(self._df.groupby(*columns))

    def distinct(self, *columns: str | daft.Expression) -> LazyDaftFrame:
        """Return distinct rows, optionally on a subset of columns.

        Raises:
            ValueError: If any string column name is not a safe identifier.
        """
        for col in columns:
            if isinstance(col, str) and not _SAFE_IDENTIFIER_RE.match(col):
                raise ValueError(f"Invalid column name '{col}'")
        if columns:
            return LazyDaftFrame(self._df.distinct(*columns))
        return LazyDaftFrame(self._df.distinct())

    def count_rows(self) -> int:
        """Count rows without full collect.

        Returns:
            Number of rows in the DataFrame.
        """
        return self._df.count_rows()

    def check_feasibility(
        self,
        warn_threshold: int = _ROW_COUNT_WARN_THRESHOLD,
        hard_limit: int = _ROW_COUNT_HARD_LIMIT,
    ) -> list[str]:
        """Pre-check if the DataFrame is safe to collect.

        Returns a list of warnings. Raises if the row count exceeds
        the hard limit — caller should route to DuckDB OLAP instead.

        Args:
            warn_threshold: Log warning above this row count.
            hard_limit: Raise above this row count.

        Returns:
            List of warning messages (empty if safe).

        Raises:
            RuntimeError: If row count exceeds hard_limit.
        """
        warnings: list[str] = []
        try:
            n = self._df.count_rows()
        except Exception:
            return warnings

        if n > hard_limit:
            raise RuntimeError(
                f"Dataset has {n:,} rows (limit {hard_limit:,}). "
                f"Use DuckDB OLAP endpoint (POST /query/olap) for large datasets."
            )
        if n > warn_threshold:
            msg = (
                f"Dataset has {n:,} rows — consider DuckDB OLAP "
                f"(POST /query/olap) for better memory efficiency."
            )
            logger.warning(msg)
            warnings.append(msg)
        return warnings

    # ── Reshape ──

    def pivot(
        self,
        group_by: str | daft.Expression,
        pivot_col: str | daft.Expression,
        value_col: str | daft.Expression,
        agg_fn: str,
        names: list[str] | None = None,
    ) -> LazyDaftFrame:
        """Pivot a column into wide format (cross-tabulation).

        Args:
            group_by: Column(s) to group by.
            pivot_col: Column whose unique values become new columns.
            value_col: Column with values to aggregate.
            agg_fn: One of "sum", "mean", "count", "min", "max", "first", "last".
            names: Optional explicit names for pivoted columns.

        Raises:
            ValueError: If agg_fn is not supported or string params are not safe identifiers.

        Returns:
            LazyDaftFrame with pivoted wide-format data.
        """
        if agg_fn not in _PIVOT_AGG_FNS:
            raise ValueError(f"Invalid agg_fn '{agg_fn}', must be one of {sorted(_PIVOT_AGG_FNS)}")
        for label, val in (("group_by", group_by), ("pivot_col", pivot_col), ("value_col", value_col)):
            if isinstance(val, str) and not _SAFE_IDENTIFIER_RE.match(val):
                raise ValueError(f"Invalid {label} '{val}'")
        if names:
            for n in names:
                if not _SAFE_IDENTIFIER_RE.match(n):
                    raise ValueError(f"Invalid pivot column name '{n}'")
        return LazyDaftFrame(
            self._df.pivot(group_by, pivot_col, value_col, agg_fn, names=names)
        )

    def unpivot(
        self,
        ids: str | daft.Expression | list[str | daft.Expression],
        values: str | daft.Expression | list[str | daft.Expression] | None = None,
        variable_name: str = "variable",
        value_name: str = "value",
    ) -> LazyDaftFrame:
        """Unpivot wide-format columns into long format (melt).

        Args:
            ids: Column(s) to keep as identifiers.
            values: Column(s) to unpivot. Empty = all non-id columns.
            variable_name: Name for the column holding original column names.
            value_name: Name for the column holding values.

        Returns:
            LazyDaftFrame with unpivoted long-format data.

        Raises:
            ValueError: If variable_name/value_name or string columns are not safe identifiers.
        """
        if not _SAFE_IDENTIFIER_RE.match(variable_name):
            raise ValueError(f"Invalid variable_name '{variable_name}'")
        if not _SAFE_IDENTIFIER_RE.match(value_name):
            raise ValueError(f"Invalid value_name '{value_name}'")
        for label, val in (("ids", ids), ("values", values)):
            if val is None:
                continue
            items = val if isinstance(val, list) else [val]
            for item in items:
                if isinstance(item, str) and not _SAFE_IDENTIFIER_RE.match(item):
                    raise ValueError(f"Invalid {label} column '{item}'")
        return LazyDaftFrame(
            self._df.unpivot(ids, values or [], variable_name=variable_name, value_name=value_name)
        )

    def explode(
        self,
        *columns: str | daft.Expression,
        ignore_empty: bool = False,
    ) -> LazyDaftFrame:
        """Explode list columns into one row per element.

        Args:
            columns: List-typed columns to explode.
            ignore_empty: Skip empty lists and nulls instead of dropping the row.

        Returns:
            LazyDaftFrame with exploded rows.

        Raises:
            ValueError: If any string column name is not a safe identifier.
        """
        for col in columns:
            if isinstance(col, str) and not _SAFE_IDENTIFIER_RE.match(col):
                raise ValueError(f"Invalid column name '{col}'")
        return LazyDaftFrame(
            self._df.explode(*columns, ignore_empty_and_null=ignore_empty)
        )

    # ── Sampling & Inspection ──

    def sample(
        self,
        fraction: float | None = None,
        size: int | None = None,
        seed: int | None = None,
    ) -> LazyDaftFrame:
        """Random sample of rows.

        Args:
            fraction: Fraction of rows to sample (0.0 - 1.0).
            size: Exact number of rows to sample.
            seed: Random seed for reproducibility.

        Raises:
            ValueError: If fraction is out of range or size is negative.

        Returns:
            LazyDaftFrame with sampled rows.
        """
        if fraction is not None and not 0.0 < fraction <= 1.0:
            raise ValueError(f"fraction must be in (0.0, 1.0], got {fraction}")
        if size is not None and size < 1:
            raise ValueError(f"size must be >= 1, got {size}")
        return LazyDaftFrame(self._df.sample(fraction=fraction, size=size, seed=seed))

    def describe(self) -> pa.Table:
        """Return column name and type summary."""
        return self._df.describe().to_arrow()

    def schema(self) -> daft.Schema:
        """Return the DataFrame schema (column names and types)."""
        return self._df.schema()

    # ── Join ──

    def join(
        self,
        other: LazyDaftFrame,
        on: str,
        how: str = "inner",
    ) -> LazyDaftFrame:
        """Join with another frame.

        Raises:
            ValueError: If join column or type is invalid.
        """
        if not _SAFE_IDENTIFIER_RE.match(on):
            raise ValueError(f"Invalid column name '{on}'")
        if how not in ("inner", "left", "outer"):
            raise ValueError(f"Invalid join type '{how}'")
        return LazyDaftFrame(self._df.join(other._df, on=on, how=how))  # type: ignore[arg-type]

    # ── SQL ──

    def sql(self, query: str) -> LazyDaftFrame:
        """Execute SQL query against this frame.

        The DataFrame is bound as ``self`` in the SQL context.
        Supports CTEs (WITH), window functions (RANK, ROW_NUMBER), and
        complex subqueries that the DuckDB OLAP bridge rejects.

        Args:
            query: SQL query referencing this frame as ``self``.

        Returns:
            New LazyDaftFrame with query results.

        Raises:
            ValueError: If query is empty, too long, or contains dangerous keywords.
        """
        if not query or not query.strip():
            raise ValueError("SQL query must not be empty")
        if len(query) > _MAX_SQL_LENGTH:
            raise ValueError(f"SQL query too long ({len(query)} chars, max {_MAX_SQL_LENGTH})")
        if _SQL_DANGEROUS_RE.search(query):
            raise ValueError("SQL query contains forbidden write/DDL keywords")
        logger.debug("Executing SQL: %.200s", query.strip())
        return LazyDaftFrame(daft.sql(query, self=self._df))

    # ── Materialization ──

    def collect(self, *, max_rows: int = _DEFAULT_COLLECT_MAX_ROWS) -> pa.Table:
        """Execute all lazy operations and return Arrow Table.

        Args:
            max_rows: Safety cap on returned rows. Excess rows are truncated
                with a warning. Set to 0 to disable the limit.

        Returns:
            Materialized Arrow Table (possibly truncated).
        """
        result = self._df.to_arrow()
        if max_rows > 0 and len(result) > max_rows:
            logger.warning(
                "collect() truncated %d -> %d rows (max_rows=%d)",
                len(result), max_rows, max_rows,
            )
            result = result.slice(0, max_rows)
        return result


class LazyGroupedFrame:
    """Lazy grouped DataFrame for aggregation operations."""

    def __init__(self, grouped: daft.dataframe.GroupedDataFrame) -> None:
        self._grouped = grouped

    def __repr__(self) -> str:
        return f"LazyGroupedFrame(grouped={self._grouped!r})"

    def agg(self, *exprs: daft.Expression) -> LazyDaftFrame:
        """Apply custom aggregation expressions.

        Args:
            exprs: Daft expressions like ``daft.col("x").sum()``.

        Returns:
            LazyDaftFrame with aggregated results.
        """
        return LazyDaftFrame(self._grouped.agg(*exprs))

    def sum(self) -> LazyDaftFrame:
        """Sum all numeric columns per group."""
        return LazyDaftFrame(self._grouped.sum())

    def mean(self) -> LazyDaftFrame:
        """Mean of all numeric columns per group."""
        return LazyDaftFrame(self._grouped.mean())

    def count(self) -> LazyDaftFrame:
        """Count rows per group."""
        return LazyDaftFrame(self._grouped.count())

    def min(self) -> LazyDaftFrame:
        """Min of all numeric columns per group."""
        return LazyDaftFrame(self._grouped.min())

    def max(self) -> LazyDaftFrame:
        """Max of all numeric columns per group."""
        return LazyDaftFrame(self._grouped.max())

    def stddev(self) -> LazyDaftFrame:
        """Standard deviation of all numeric columns per group."""
        return LazyDaftFrame(self._grouped.stddev())

    def var(self) -> LazyDaftFrame:
        """Variance of all numeric columns per group."""
        return LazyDaftFrame(self._grouped.var())

    def collect(self, *, max_rows: int = _DEFAULT_COLLECT_MAX_ROWS) -> pa.Table:
        """Materialize with default aggregation (sum all numeric columns).

        Args:
            max_rows: Safety cap on returned rows. 0 disables the limit.
        """
        result = self._grouped.sum().to_arrow()
        if max_rows > 0 and len(result) > max_rows:
            logger.warning(
                "GroupedFrame.collect() truncated %d -> %d rows", len(result), max_rows,
            )
            result = result.slice(0, max_rows)
        return result


class DaftQueryEngine:
    """Query engine using Daft for Lance datasets.

    Provides lazy DataFrame API for efficient data processing.
    All operations are lazy until .collect() is called.

    Args:
        base_uri: Base URI for Lance dataset storage.
        storage_config: Optional StorageConfig for S3/MinIO access.
        daft_config: Optional DaftConfig for performance tuning.
    """

    def __init__(
        self,
        base_uri: str | Path,
        storage_config: Any | None = None,
        daft_config: Any | None = None,
    ) -> None:
        self.base_uri = str(base_uri)
        self._storage_config = storage_config
        self._daft_config = daft_config
        self._io_config: Any = None
        if storage_config is not None:
            self._io_config = self._build_io_config(storage_config)
        if daft_config is not None:
            self._apply_planning_config(daft_config)

    def __repr__(self) -> str:
        return f"DaftQueryEngine(base_uri='{self.base_uri}')"

    @staticmethod
    def _apply_planning_config(daft_config: Any) -> None:
        """Apply Daft planning config from DaftConfig.

        Daft >= 0.4 removed default_num_partitions and target_partition_max_memory_bytes
        from set_planning_config. We apply them only when available.
        """
        sig = inspect.signature(daft.set_planning_config)
        available = sig.parameters
        kwargs: dict[str, Any] = {}
        if "default_num_partitions" in available:
            kwargs["default_num_partitions"] = daft_config.default_num_partitions
        if "target_partition_max_memory_bytes" in available:
            kwargs["target_partition_max_memory_bytes"] = daft_config.target_partition_max_memory_bytes
        if kwargs:
            daft.set_planning_config(**kwargs)

    @staticmethod
    def _build_io_config(storage_config: Any) -> Any:
        """Build Daft IOConfig for S3/MinIO access."""
        from arrow_lake.config import StorageBackend

        if getattr(storage_config, "backend", None) == StorageBackend.LOCAL:
            return None
        from daft.io import IOConfig, S3Config

        use_ssl = storage_config.s3_endpoint.startswith("https://")
        return IOConfig(
            s3=S3Config(
                region_name=storage_config.s3_region,
                endpoint_url=storage_config.s3_endpoint,
                key_id=storage_config.s3_access_key,
                access_key=storage_config.s3_secret_key,
                use_ssl=use_ssl,
                verify_ssl=use_ssl,
            )
        )

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
            FileNotFoundError: If dataset does not exist.
        """
        if not dataset_name or not _SAFE_IDENTIFIER_RE.match(dataset_name):
            raise ValueError(f"Invalid dataset name '{dataset_name}'")
        if columns:
            for col in columns:
                if not _SAFE_IDENTIFIER_RE.match(col):
                    raise ValueError(f"Invalid column name '{col}'")

        if self._io_config is not None:
            lance_path = f"{self._storage_config.s3_uri.rstrip('/')}/{dataset_name}.lance"
        else:
            lance_path = str(Path(self.base_uri) / f"{dataset_name}.lance")

        read_kwargs: dict[str, Any] = {}
        if self._io_config is not None:
            read_kwargs["io_config"] = self._io_config

        try:
            df = daft.read_lance(lance_path, **read_kwargs)
        except (FileNotFoundError, ValueError) as exc:
            raise FileNotFoundError(
                f"Dataset '{dataset_name}' not found"
            ) from exc
        except Exception as exc:
            logger.error("Failed to load dataset '%s': %s", dataset_name, exc)
            raise RuntimeError(
                f"Failed to load dataset '{dataset_name}'"
            ) from exc

        if columns:
            df = df.select(*columns)
        return LazyDaftFrame(df)
