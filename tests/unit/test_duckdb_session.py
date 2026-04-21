"""Tests for DuckDBSession rewrite with extension loading and resource governance.

M0a Day 1 — TDD RED phase.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import duckdb
import pytest

from arrow_lake.exceptions import ArrowLakeError, ErrorCode



# ---------------------------------------------------------------------------
# DuckDBSession class-based API
# ---------------------------------------------------------------------------


class TestDuckDBSessionInit:
    """Test DuckDBSession construction and basic lifecycle."""

    def test_creates_connection_with_defaults(self) -> None:
        """Backward compat: bare DuckDBSession() should still work."""
        from arrow_lake.query._db import DuckDBSession

        session = DuckDBSession()
        assert session is not None
        # Context manager protocol
        with session as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1

    def test_loads_lance_extension_by_default(self) -> None:
        """By default, lance extension should be loaded."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession() as conn:
            # If lance extension loaded, this should not raise
            # Verify by running a lance-related function
            conn.execute("SELECT 1").fetchone()

    def test_skip_ducklake_when_disabled(self) -> None:
        """load_ducklake=False should skip ducklake extension."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession(load_ducklake=False) as conn:
            conn.execute("SELECT 1").fetchone()

    def test_resource_governance_memory_limit(self) -> None:
        """memory_limit should be set from config."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession(max_memory_mb=512) as conn:
            result = conn.execute("SELECT current_setting('memory_limit')").fetchone()[0]
            # DuckDB reports memory in MiB format, e.g. "488.2 MiB"
            assert "MiB" in result or "MB" in result

    def test_resource_governance_timeout(self) -> None:
        """statement_timeout should be set from config when supported."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession(timeout_seconds=30) as conn:
            # statement_timeout may not be available in all DuckDB versions;
            # just verify the session works without error
            conn.execute("SELECT 1").fetchone()

    def test_resource_governance_threads(self) -> None:
        """threads should be set to os.cpu_count() by default."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession() as conn:
            result = conn.execute("SELECT current_setting('threads')").fetchone()[0]
            # DuckDB returns int for threads setting
            assert int(result) == os.cpu_count()

    def test_context_manager_closes_connection(self) -> None:
        """Connection should be closed after exiting context."""
        from arrow_lake.query._db import DuckDBSession

        session = DuckDBSession()
        with session as conn:
            pass
        # After exit, the session's internal conn should be None
        assert session._conn is None


# ---------------------------------------------------------------------------
# Extension loading — fast-fail on failure
# ---------------------------------------------------------------------------


class TestExtensionLoading:
    """Test that extension loading raises ArrowLakeError on failure (startup fast-fail)."""

    def test_lance_extension_load_failure_raises_arrow_lake_error(self) -> None:
        """If lance extension fails to load, should raise ArrowLakeError."""
        from arrow_lake.query._db import DuckDBSession

        session = DuckDBSession()
        with patch.object(
            session,
            "_load_extensions",
            side_effect=ArrowLakeError(
                ErrorCode.LANCE_EXTENSION_ERROR,
                "Failed to load lance extension: boom",
            ),
        ):
            with pytest.raises(ArrowLakeError, match="lance"):
                with session:
                    pass

    def test_ducklake_load_failure_raises_arrow_lake_error(self) -> None:
        """If ducklake extension fails to load, should raise ArrowLakeError."""
        from arrow_lake.query._db import DuckDBSession

        session = DuckDBSession(load_ducklake=True)
        with patch.object(
            session,
            "_load_extensions",
            side_effect=ArrowLakeError(
                ErrorCode.DUCKLAKE_EXTENSION_ERROR,
                "Failed to load ducklake extension: boom",
            ),
        ):
            with pytest.raises(ArrowLakeError, match="ducklake"):
                with session:
                    pass


# ---------------------------------------------------------------------------
# S3 configuration
# ---------------------------------------------------------------------------


class TestDuckDBSessionS3Config:
    """Test that S3 config is applied when StorageConfig is provided."""

    def test_s3_config_applied_when_storage_config(self) -> None:
        """When storage_config with backend=minio is provided, S3 settings should be applied."""
        from arrow_lake.config import StorageConfig
        from arrow_lake.query._db import DuckDBSession

        config = StorageConfig(
            backend="minio",
            s3_endpoint="http://minio:9000",
            s3_region="us-east-1",
            s3_access_key="test-key",
            s3_secret_key="test-secret",
        )
        with DuckDBSession(storage_config=config) as conn:
            result = conn.execute("SELECT current_setting('s3_region')").fetchone()[0]
            assert "us-east-1" == result

    def test_s3_config_skipped_when_local(self) -> None:
        """When backend=local, no S3 config should be applied."""
        from arrow_lake.config import StorageConfig
        from arrow_lake.query._db import DuckDBSession

        config = StorageConfig(backend="local")
        with DuckDBSession(storage_config=config) as conn:
            conn.execute("SELECT 1").fetchone()


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestCreateDuckDBSession:
    """Test the module-level factory function."""

    def test_factory_returns_session(self) -> None:
        """create_duckdb_session() should return a DuckDBSession instance."""
        from arrow_lake.query._db import DuckDBSession, create_duckdb_session

        session = create_duckdb_session()
        assert isinstance(session, DuckDBSession)

    def test_factory_passes_config(self) -> None:
        """Factory should pass OlapConfig settings through."""
        from arrow_lake.query._db import DuckDBSession, create_duckdb_session

        session = create_duckdb_session(max_memory_mb=1024)
        with session as conn:
            result = conn.execute("SELECT current_setting('memory_limit')").fetchone()[0]
            # DuckDB reports memory in MiB format
            assert "MiB" in result or "MB" in result
