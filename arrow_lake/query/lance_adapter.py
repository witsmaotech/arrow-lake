"""LanceScanAdapter — abstract layer for Lance → DuckDB bridging.

Provides two scan strategies:
- **Native**: Uses DuckDB lance extension's ``__lance_scan()`` SQL function
- **PyArrow Fallback**: Uses PyArrow ``dataset.scanner().to_reader()`` for
  streaming (avoids ``to_table()`` OOM on large datasets)

A factory function ``create_lance_scan_adapter()`` selects the best strategy.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from contextlib import contextmanager, suppress
from typing import Any

import duckdb

from arrow_lake.exceptions import ArrowLakeError, ErrorCode
from arrow_lake.validation import validate_identifier

logger = logging.getLogger(__name__)


def _esc(s: str) -> str:
    """Escape single quotes for SQL string interpolation."""
    return s.replace("'", "''")

__all__ = [
    "LanceScanAdapter",
    "NativeLanceScanAdapter",
    "PyArrowFallbackAdapter",
    "create_lance_scan_adapter",
]


class LanceScanAdapter(ABC):
    """Abstract base class for Lance dataset scanning strategies.

    Subclasses must implement ``scan()``, ``create_view()``, and ``is_available()``.
    """

    @abstractmethod
    def scan(
        self,
        conn: Any,
        uri: str,
        *,
        columns: list[str] | None = None,
    ) -> contextmanager[Any]:
        """Scan a Lance dataset and return a DuckDB context with data registered.

        Args:
            conn: Active DuckDB connection.
            uri: Lance dataset URI (local path or s3:// URI).
            columns: Optional column subset to scan.

        Yields:
            DuckDB connection with data registered as table ``t``.
        """
        ...

    @abstractmethod
    def create_view(
        self,
        conn: Any,
        uri: str,
        view_name: str,
        *,
        columns: list[str] | None = None,
    ) -> None:
        """Create a named DuckDB view from a Lance dataset.

        Args:
            conn: Active DuckDB connection.
            uri: Lance dataset URI.
            view_name: Name for the DuckDB view.
            columns: Optional column subset.
        """
        ...

    @abstractmethod
    def is_available(self, conn: Any | None = None) -> bool:
        """Check whether this adapter's prerequisites are met.

        Args:
            conn: Optional DuckDB connection for runtime checks.

        Returns:
            True if the adapter can be used.
        """
        ...


class NativeLanceScanAdapter(LanceScanAdapter):
    """Scans Lance datasets using DuckDB's ``__lance_scan()`` SQL function.

    Requires the DuckDB lance extension to be loaded. Provides zero-copy
    access but cannot create Lance indexes (that requires LanceDB SDK).
    """

    def __init__(self) -> None:
        self._available: bool | None = None

    def _load_lance_scan(self, conn: Any) -> bool:
        """Probe whether __lance_scan is available in the current connection."""
        try:
            result = conn.execute(
                "SELECT count(*) FROM duckdb_functions() WHERE function_name = '__lance_scan'"
            ).fetchone()
            available = result is not None and result[0] > 0
        except (duckdb.Error, TypeError):
            available = False
        self._available = available
        return available

    def is_available(self, conn: Any | None = None) -> bool:
        if self._available is not None:
            return self._available
        if conn is None:
            return False
        return self._load_lance_scan(conn)

    @contextmanager
    def scan(
        self,
        conn: Any,
        uri: str,
        *,
        columns: list[str] | None = None,
    ) -> contextmanager[Any]:
        """Yield connection with Lance dataset registered as table ``t``."""
        if not self.is_available(conn):
            raise ArrowLakeError(
                ErrorCode.LANCE_EXTENSION_ERROR,
                "Native lance scan is not available (lance extension not loaded)",
            )
        conn.execute(
            f"CREATE OR REPLACE TABLE t AS SELECT * FROM __lance_scan('{_esc(uri)}', explain_verbose := false)"  # nosec B608
        )
        try:
            yield conn
        finally:
            with suppress(Exception):
                conn.execute("DROP TABLE IF EXISTS t")

    def create_view(
        self,
        conn: Any,
        uri: str,
        view_name: str,
        *,
        columns: list[str] | None = None,
    ) -> None:
        """Create a DuckDB VIEW backed by ``__lance_scan()``."""
        validate_identifier(view_name)
        conn.execute(
            f"CREATE OR REPLACE VIEW {view_name} AS "  # nosec B608
            f"SELECT * FROM __lance_scan('{_esc(uri)}', explain_verbose := false)"
        )


class PyArrowFallbackAdapter(LanceScanAdapter):
    """Scans Lance datasets using PyArrow ``dataset.scanner().to_reader()``.

    Streams data via RecordBatchReader to avoid OOM from ``to_table()``
    on large datasets. This is the safe fallback when the DuckDB lance
    extension is not available.
    """

    def __init__(self, dataset: object) -> None:
        """Initialize with a Lance dataset object.

        Args:
            dataset: A Lance dataset object with ``scanner()`` method.
        """
        self._dataset = dataset

    def is_available(self, conn: Any | None = None) -> bool:
        return True

    @contextmanager
    def scan(
        self,
        conn: Any,
        uri: str,
        *,
        columns: list[str] | None = None,
    ) -> contextmanager[Any]:
        """Yield connection with Lance dataset registered as table ``t``."""
        scanner_kwargs: dict = {}
        if columns is not None:
            scanner_kwargs["columns"] = columns

        reader = self._dataset.scanner(**scanner_kwargs).to_reader()
        conn.register("t", reader)
        try:
            yield conn
        finally:
            with suppress(Exception):
                conn.execute("DROP TABLE IF EXISTS t")

    def create_view(
        self,
        conn: Any,
        uri: str,
        view_name: str,
        *,
        columns: list[str] | None = None,
    ) -> None:
        """Register Lance dataset as a DuckDB table (DuckDB doesn't support
        CREATE VIEW from a registered reader, so we register directly)."""
        scanner_kwargs: dict = {}
        if columns is not None:
            scanner_kwargs["columns"] = columns

        reader = self._dataset.scanner(**scanner_kwargs).to_reader()
        conn.register(view_name, reader)


def create_lance_scan_adapter(
    conn: Any,
    *,
    mode: str = "auto",
    dataset: object | None = None,
) -> LanceScanAdapter:
    """Factory function that creates the appropriate LanceScanAdapter.

    Args:
        conn: DuckDB connection for probing native availability.
        mode: Scan mode — "auto", "native", or "pyarrow_fallback".
        dataset: Lance dataset object (required for pyarrow_fallback mode).

    Returns:
        A LanceScanAdapter instance.

    Raises:
        ValueError: If mode is invalid or dataset is required but not provided.
        ArrowLakeError: If native mode is requested but not available.
    """
    valid_modes = {"auto", "native", "pyarrow_fallback"}
    if mode not in valid_modes:
        raise ValueError(f"mode must be one of {valid_modes}, got {mode!r}")

    if mode == "native":
        adapter = NativeLanceScanAdapter()
        if not adapter.is_available(conn):
            raise ArrowLakeError(
                ErrorCode.LANCE_EXTENSION_ERROR,
                "Native lance scan requested but lance extension is not available",
            )
        return adapter

    if mode == "pyarrow_fallback":
        if dataset is None:
            raise ValueError("dataset is required for pyarrow_fallback mode")
        return PyArrowFallbackAdapter(dataset=dataset)

    # mode == "auto"
    native = NativeLanceScanAdapter()
    if native.is_available(conn):
        logger.debug("Using native lance scan adapter")
        return native

    if dataset is None:
        raise ValueError("dataset is required when falling back to pyarrow_fallback in auto mode")
    logger.debug("Falling back to PyArrow scan adapter")
    return PyArrowFallbackAdapter(dataset=dataset)
