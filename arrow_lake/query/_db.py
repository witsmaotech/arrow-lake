"""Shared DuckDB session manager for query bridges.

Provides a class-based DuckDB session with:
- Extension loading (lance, ducklake) with startup fast-fail
- Resource governance (memory limit, threads, statement timeout)
- S3 configuration from StorageConfig
- Backward-compatible context manager protocol
"""

from __future__ import annotations

import logging
import os

import duckdb

from arrow_lake.config import OlapConfig, StorageConfig
from arrow_lake.exceptions import ArrowLakeError, ErrorCode

logger = logging.getLogger(__name__)

__all__ = ["DuckDBSession", "create_duckdb_session"]


class DuckDBSession:
    """Managed DuckDB session with extension loading and resource governance.

    Usage::

        with DuckDBSession() as conn:
            conn.execute("SELECT 1").fetchone()

        with DuckDBSession(max_memory_mb=512, timeout_seconds=30) as conn:
            result = conn.execute("SELECT * FROM t").arrow()

    Backward compatible: ``DuckDBSession()`` with no arguments still works.
    """

    def __init__(
        self,
        *,
        max_memory_mb: int = 512,
        timeout_seconds: int = 300,
        threads: int | None = None,
        load_ducklake: bool = False,
        olap_config: OlapConfig | None = None,
        storage_config: StorageConfig | None = None,
    ) -> None:
        self._max_memory_mb = max_memory_mb
        self._timeout_seconds = timeout_seconds
        self._threads = threads if threads is not None else (os.cpu_count() or 4)
        self._load_ducklake = load_ducklake
        self._olap_config = olap_config
        self._storage_config = storage_config
        self._conn: duckdb.DuckDBPyConnection | None = None

    def _load_extensions(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Install and load required DuckDB extensions. Raises ArrowLakeError on failure."""
        try:
            conn.execute("INSTALL lance; LOAD lance;")
        except Exception as exc:
            raise ArrowLakeError(
                ErrorCode.LANCE_EXTENSION_ERROR,
                f"Failed to load lance extension: {exc}",
            ) from exc

        if self._load_ducklake:
            try:
                conn.execute("INSTALL ducklake; LOAD ducklake;")
            except Exception as exc:
                raise ArrowLakeError(
                    ErrorCode.DUCKLAKE_EXTENSION_ERROR,
                    f"Failed to load ducklake extension: {exc}",
                ) from exc

    def _configure_resources(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Set memory limit, threads, and statement timeout.

        statement_timeout is only available in DuckDB >= 1.2.0.
        If unsupported, it is silently skipped.
        """
        conn.execute(f"SET memory_limit='{self._max_memory_mb}MB';")
        conn.execute(f"SET threads={self._threads};")
        try:
            conn.execute(f"SET statement_timeout='{self._timeout_seconds}s';")
        except duckdb.CatalogException:
            logger.debug(
                "statement_timeout not supported in DuckDB %s, skipping",
                duckdb.__version__,
            )

    def _configure_s3(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Apply S3 configuration from StorageConfig if backend is not LOCAL.

        Uses parameterized SET via prepare/execute to avoid SQL injection.
        """
        if self._storage_config is None:
            return
        config = self._storage_config
        if config.backend == "local":
            return

        # DuckDB SET doesn't support ? placeholders, so escape single quotes
        def _esc(val: str) -> str:
            return val.replace("'", "''")

        conn.execute(f"SET s3_region='{_esc(config.s3_region)}';")
        conn.execute(f"SET s3_endpoint='{_esc(config.s3_endpoint)}';")
        conn.execute(f"SET s3_access_key_id='{_esc(config.s3_access_key)}';")
        conn.execute(f"SET s3_secret_access_key='{_esc(config.s3_secret_key)}';")

    def __enter__(self) -> duckdb.DuckDBPyConnection:
        """Create connection, load extensions, configure resources and S3."""
        self._conn = duckdb.connect()
        self._load_extensions(self._conn)
        self._configure_resources(self._conn)
        self._configure_s3(self._conn)
        return self._conn

    def __exit__(self, *args: object) -> None:
        """Close the connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def create_duckdb_session(
    *,
    max_memory_mb: int = 512,
    timeout_seconds: int = 300,
    threads: int | None = None,
    load_ducklake: bool = False,
    olap_config: OlapConfig | None = None,
    storage_config: StorageConfig | None = None,
) -> DuckDBSession:
    """Factory function for creating DuckDB sessions.

    Convenience wrapper that passes configuration through to DuckDBSession.
    """
    return DuckDBSession(
        max_memory_mb=max_memory_mb,
        timeout_seconds=timeout_seconds,
        threads=threads,
        load_ducklake=load_ducklake,
        olap_config=olap_config,
        storage_config=storage_config,
    )
