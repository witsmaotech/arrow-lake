"""P0-1 (C1): DuckDB local-filesystem lockdown tests.

Guards against `SELECT * FROM read_text('/proc/self/environ')` class of
platform-level secret exfiltration through user-facing SQL endpoints.

Two layers (defense in depth):
1. SQL validation blacklist on table functions (validation.py).
2. Session-level `SET disabled_filesystems` (query/_db.py + session_manager
   idle-connection reapply path).
"""

from __future__ import annotations

import pytest

from arrow_lake.validation import validate_sql_safety, validate_where_clause


class TestTableFunctionBlacklist:
    """validate_sql_safety must reject DuckDB reader/glob table functions."""

    @pytest.mark.parametrize(
        "sql",
        [
            # Local file exfiltration vectors (env vars / internal files).
            "SELECT * FROM read_text('/proc/self/environ')",
            "SELECT * FROM read_csv('/data/lake/.console/dataset_descriptions.json')",
            "SELECT * FROM read_json('/data/lake/ka/ds/ka/data.json')",
            "SELECT * FROM read_parquet('/etc/passwd.parquet')",
            "SELECT * FROM read_blob('/proc/self/environ')",
            "SELECT * FROM glob('/data/lake/**/*.json')",
            "SELECT * FROM file_search('/data/lake', 'secret')",
            # Auto-variant table functions (word-char suffix must not dodge
            # the plain-name boundary).
            "SELECT * FROM read_csv_auto('/etc/hosts')",
            "SELECT * FROM read_json_auto('/etc/hosts')",
            # Remote/object-storage scans bypassing dataset ACLs.
            "SELECT * FROM read_parquet('s3://arrow-lake/raw/x.parquet')",
            "SELECT * FROM parquet_scan('s3://arrow-lake/raw/x.parquet')",
            "SELECT * FROM iceberg_scan('s3://bucket/warehouse/t')",
            "SELECT * FROM delta_scan('s3://bucket/delta/t')",
            "SELECT * FROM postgres_scan('host=127.0.0.1 dbname=x')",
            # Case-insensitive.
            "SELECT * FROM READ_TEXT('/proc/self/environ')",
            "select * from Read_Csv('/etc/hosts')",
            # Inside expressions, not only FROM.
            "SELECT length(read_text('/proc/self/environ'))",
        ],
    )
    def test_reader_table_functions_rejected(self, sql: str) -> None:
        with pytest.raises(ValueError, match="table function"):
            validate_sql_safety(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            # Legit user SQL must stay untouched.
            "SELECT id, name FROM users WHERE age > 18",
            "WITH cte AS (SELECT 1) SELECT * FROM cte",
            # Column names that merely contain blacklisted substrings.
            "SELECT read_time, csv_path FROM telemetry",
            "SELECT glob_score FROM jobs",
        ],
    )
    def test_legitimate_sql_unaffected(self, sql: str) -> None:
        validate_sql_safety(sql)

    def test_range_in_table_position_now_rejected(self):
        """v1.10.7 structural whitelist: table position accepts NAMED DATASETS
        only — range() was previously tolerated, but allowing any table-valued
        function (even a harmless generator) reopens the enumeration game the
        whitelist exists to end. Deliberate behavior change."""
        with pytest.raises(ValueError, match="named dataset"):
            validate_sql_safety("SELECT * FROM range(10)")

    def test_where_clause_also_checked(self) -> None:
        with pytest.raises(ValueError, match="table function"):
            validate_where_clause("x = 1 OR read_text('/etc/hosts') IS NOT NULL")


class _FakeConn:
    """Records execute() calls; no real DuckDB needed for SET assertions."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str) -> None:  # noqa: D102
        self.executed.append(sql)


class TestSessionFilesystemLockdown:
    """DuckDBSession must disable local filesystem scanning on every session."""

    def _configured_conn(self, olap_config=None) -> _FakeConn:
        from arrow_lake.query._db import DuckDBSession

        session = DuckDBSession(
            max_memory_mb=64,
            timeout_seconds=5,
            olap_config=olap_config,
        )
        fake = _FakeConn()
        # v1.10.7 fix: lockdown moved out of _configure_resources (it ran before
        # _configure_s3's CREATE SECRET, which needs LocalFileSystem)
        session._lockdown_filesystems(fake)
        return fake

    def test_default_session_disables_local_filesystem(self) -> None:
        fake = self._configured_conn()
        assert any(
            "disabled_filesystems" in sql
            and "LocalFileSystem" in sql
            for sql in fake.executed
        ), f"disabled_filesystems SET missing: {fake.executed}"

    def test_olap_config_can_extend_disabled_filesystems(self) -> None:
        from arrow_lake.config import OlapConfig

        cfg = OlapConfig(disabled_filesystems=["LocalFileSystem", "S3"])
        fake = self._configured_conn(olap_config=cfg)
        sets = [s for s in fake.executed if "disabled_filesystems" in s]
        assert sets, "disabled_filesystems SET missing"
        assert "S3" in sets[0] and "LocalFileSystem" in sets[0]

    def test_lockdown_is_applied_before_session_returns(self) -> None:
        """Full __enter__ path applies lockdown even without olap_config."""
        from arrow_lake.query._db import DuckDBSession

        session = DuckDBSession()
        fake = _FakeConn()
        # Patch post-connection steps to avoid network extension installs.
        session._load_extensions = lambda conn: None  # type: ignore[method-assign]
        session._configure_s3 = lambda conn: None  # type: ignore[method-assign]
        original_connect = __import__("duckdb").connect
        try:
            __import__("arrow_lake.query._db", fromlist=["duckdb"])
            import arrow_lake.query._db as db_mod

            db_mod.duckdb.connect = lambda *a, **k: fake  # type: ignore[assignment]
            conn = session.__enter__()
            assert conn is fake
        finally:
            import arrow_lake.query._db as db_mod

            db_mod.duckdb.connect = original_connect  # type: ignore[assignment]
        assert any("disabled_filesystems" in s for s in fake.executed)

    def test_lockdown_is_the_last_step_of_enter(self):
        """Regression (console preview 500, 2026-08-24): the fs lockdown ran
        inside _configure_resources, BEFORE _configure_s3 — DuckDB's CREATE
        SECRET needs LocalFileSystem, so every OLAP query died with
        PermissionException → 500. The lockdown constrains USER SQL only and
        must run after ALL engine self-configuration."""
        from types import SimpleNamespace

        import arrow_lake.query._db as db_mod
        from arrow_lake.query._db import DuckDBSession

        session = DuckDBSession(
            storage_config=SimpleNamespace(
                backend="minio",
                s3_endpoint="http://minio:9000",
                s3_access_key="ak",
                s3_secret_key="sk",
                s3_region="us-east-1",
            )
        )
        session._load_extensions = lambda conn: None  # type: ignore[method-assign]
        fake = _FakeConn()
        original_connect = db_mod.duckdb.connect
        try:
            db_mod.duckdb.connect = lambda *a, **k: fake  # type: ignore[assignment]
            session.__enter__()
        finally:
            db_mod.duckdb.connect = original_connect  # type: ignore[assignment]

        idx = [i for i, s in enumerate(fake.executed) if "disabled_filesystems" in s]
        assert idx, f"lockdown missing: {fake.executed}"
        assert idx[0] == len(fake.executed) - 1, (
            f"lockdown not last (engine config after it would hit the disabled "
            f"LocalFileSystem, e.g. CREATE SECRET): {fake.executed}"
        )
