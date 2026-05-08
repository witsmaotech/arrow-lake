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
        storage_config: Optional StorageConfig for S3/MinIO access.
    """

    def __init__(
        self,
        base_uri: str | Path,
        storage_config: Any | None = None,
    ) -> None:
        self.base_uri = str(base_uri)
        self._storage_config = storage_config
        self._io_config: Any = None
        if storage_config is not None:
            self._io_config = self._build_io_config(storage_config)

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
        """
        if not _SAFE_IDENTIFIER_RE.match(dataset_name):
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
        df = daft.read_lance(lance_path, **read_kwargs)
        if columns:
            df = df.select(*columns)
        return LazyDaftFrame(df)
