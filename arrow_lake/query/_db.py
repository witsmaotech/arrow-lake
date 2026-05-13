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
        except duckdb.CatalogException as exc:
            raise ArrowLakeError(
                ErrorCode.LANCE_EXTENSION_ERROR,
                f"Failed to load lance extension: {exc}",
            ) from exc

        if self._load_ducklake:
            try:
                conn.execute("INSTALL ducklake; LOAD ducklake;")
            except duckdb.CatalogException as exc:
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

        Sets both DuckDB SET variables (for httpfs) and environment variables
        (for lance extension's Rust AWS SDK).
        """
        if self._storage_config is None:
            return
        config = self._storage_config
        if config.backend == "local":
            return

        # DuckDB SET doesn't support ? placeholders, so escape single quotes
        def _esc(val: str) -> str:
            return val.replace("'", "''")

        # Strip protocol prefix for DuckDB (it adds its own)
        endpoint = config.s3_endpoint
        if endpoint.startswith("http://"):
            endpoint = endpoint[len("http://") :]
        elif endpoint.startswith("https://"):
            endpoint = endpoint[len("https://") :]
        is_http = not config.s3_endpoint.startswith("https://")

        conn.execute(f"SET s3_region='{_esc(config.s3_region)}';")
        conn.execute(f"SET s3_endpoint='{_esc(endpoint)}';")
        conn.execute(f"SET s3_access_key_id='{_esc(config.s3_access_key)}';")
        conn.execute(f"SET s3_secret_access_key='{_esc(config.s3_secret_key)}';")
        if is_http:
            conn.execute("SET s3_use_ssl=false;")
            conn.execute("SET s3_url_style='path';")

        # Set environment variables for lance extension's Rust AWS SDK.
        # The lance DuckDB extension uses object_store::aws::AmazonS3Builder
        # whose with_env_s3() parses env vars via AmazonS3ConfigKey::from_str().
        # It does NOT recognize AWS_ENDPOINT_URL_S3 (the AWS SDK standard name),
        # only AWS_ENDPOINT_URL and its aliases.
        # Ref: lancedb/lance rust/lance-io/src/object_store/providers/aws.rs
        # NOTE: These env vars are required by lance's Rust SDK. We save/restore
        # to minimize credential exposure to child processes.
        _env_backup = {
            k: os.environ.get(k) for k in (
                "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                "AWS_REGION", "AWS_ENDPOINT_URL", "AWS_ALLOW_HTTP",
            )
        }
        self._s3_env_backup = _env_backup
        try:
            os.environ["AWS_ACCESS_KEY_ID"] = config.s3_access_key
            os.environ["AWS_SECRET_ACCESS_KEY"] = config.s3_secret_key
            os.environ["AWS_REGION"] = config.s3_region
            os.environ["AWS_ENDPOINT_URL"] = config.s3_endpoint
            if is_http:
                os.environ["AWS_ALLOW_HTTP"] = "true"
        except Exception:
            # Restore on partial failure
            for k, v in _env_backup.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def __enter__(self) -> duckdb.DuckDBPyConnection:
        """Create connection, load extensions, configure resources and S3."""
        self._conn = duckdb.connect()
        self._load_extensions(self._conn)
        self._configure_resources(self._conn)
        self._configure_s3(self._conn)
        return self._conn

    def __exit__(self, *args: object) -> None:
        """Close the connection and restore S3 env vars."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        backup = getattr(self, "_s3_env_backup", None)
        if backup:
            for k, v in backup.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


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
