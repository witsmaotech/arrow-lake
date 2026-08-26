"""P0-6 (review 2026-08-26): pooled DuckDB sessions must return clean.

``_register_dataset`` used to leave the arrow registration (and the
two-part schema-qualified VIEW) on the pooled connection; a later user of
the same session could reference those objects by name and read another
user's data — reproduced live with a registered ``secret`` table. The
queries now unregister everything in a finally block. These tests hold a
REAL DuckDB connection across two queries and prove the second query
cannot see the first query's registrations.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import duckdb
import pyarrow as pa
import pytest

from arrow_lake.config import OlapConfig
from arrow_lake.exceptions import QueryError
from arrow_lake.query.olap import OlapSearchBridge


class _SharedSession:
    """Stands in for a pooled _ManagedSession: one real conn, reused."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn

    def __enter__(self) -> duckdb.DuckDBPyConnection:
        return self._conn

    def __exit__(self, *args: Any) -> bool:
        return False


class _FakeStorage:
    """Minimal storage: read_dataset serves named arrow tables."""

    def __init__(self, tables: dict[str, pa.Table]) -> None:
        self._tables = tables

    def read_dataset(self, name: str, table: str | None = None) -> pa.Table:
        key = f"{name}.{table}" if table else name
        if key not in self._tables:
            raise FileNotFoundError(key)
        return self._tables[key]

    def list_container_tables(self, name: str) -> list[str]:
        return []


def _bridge(tables: dict[str, pa.Table]) -> tuple[OlapSearchBridge, duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    bridge = OlapSearchBridge(
        _FakeStorage(tables),
        config=OlapConfig(
            lance_scan_mode="pyarrow_fallback",
            enable_streaming=False,
            query_cache_enabled=False,
        ),
    )
    assert bridge._session_manager is None
    bridge._managed_session = lambda **_kw: _SharedSession(conn)  # type: ignore[method-assign]
    return bridge, conn


class TestRegistrationCleanup:
    def test_second_user_cannot_read_first_users_registration(self) -> None:
        """The reproduced leak: after querying ``secret`` on a pooled conn,
        an unrelated later query must NOT be able to SELECT from it."""
        bridge, conn = _bridge({
            "secret": pa.table({"v": [1, 2, 3]}),
            "public_ds": pa.table({"v": [9]}),
        })
        bridge.query("secret", "SELECT COUNT(*) AS c FROM secret")
        # The registration is gone: a bare reference now fails to bind
        # (raised raw by DuckDB or wrapped as QueryError by the bridge —
        # either way, the data is NOT readable).
        with pytest.raises((QueryError, duckdb.Error)):
            bridge.query("public_ds", "SELECT COUNT(*) AS c FROM secret")

    def test_two_part_view_and_schema_removed(self) -> None:
        bridge, conn = _bridge({
            "gas.segments": pa.table({"v": [1]}),
            "other": pa.table({"v": [2]}),
        })
        bridge.query("gas.segments", "SELECT COUNT(*) AS c FROM gas.segments")
        # The schema-qualified view (and its schema) must be dropped.
        views = conn.execute(
            "SELECT schema_name, view_name FROM duckdb_views() "
            "WHERE NOT internal AND schema_name NOT IN ('temp', 'main')",
        ).fetchall()
        assert views == []
        schemas = conn.execute(
            "SELECT schema_name FROM duckdb_schemas() "
            "WHERE NOT internal AND schema_name NOT IN ('temp', 'main')",
        ).fetchall()
        assert schemas == []

    def test_extra_table_names_unregistered(self) -> None:
        bridge, conn = _bridge({"ds": pa.table({"v": [1]})})
        extra = pa.table({"e": [7]})
        bridge.query("ds", "SELECT COUNT(*) AS c FROM ds, extra_t", tables={"extra_t": extra})
        # extra_t was registered for the JOIN and must be gone afterwards.
        remaining = conn.execute(
            "SELECT view_name FROM duckdb_views() WHERE view_name = 'extra_t'",
        ).fetchall()
        assert remaining == []

    def test_cleanup_registration_names(self) -> None:
        """Unit: the flat internal name is what gets unregistered."""
        bridge, _ = _bridge({})
        mock_conn = MagicMock()
        bridge._cleanup_registration(mock_conn, "gas.segments")
        mock_conn.unregister.assert_called_once_with("_al__gas__segments")
        executed = " ".join(str(c.args[0]) for c in mock_conn.execute.call_args_list)
        assert '"gas"."segments"' in executed  # view drop
        assert '"gas"' in executed  # schema drop

    def test_query_result_unaffected(self) -> None:
        """Cleanup is symmetric: a query still sees its own registration."""
        bridge, conn = _bridge({"ds": pa.table({"v": [1, 2]})})
        result = bridge.query("ds", "SELECT SUM(v) AS s FROM ds")
        assert result.table.column("s").to_pylist() == [3]


def _bridge_class(storage: Any) -> tuple[OlapSearchBridge, duckdb.DuckDBPyConnection]:
    """Bridge over a caller-supplied storage class (shares one real conn)."""
    conn = duckdb.connect(":memory:")
    bridge = OlapSearchBridge(
        storage,
        config=OlapConfig(
            lance_scan_mode="pyarrow_fallback",
            enable_streaming=False,
            query_cache_enabled=False,
        ),
    )
    bridge._managed_session = lambda **_kw: _SharedSession(conn)  # type: ignore[method-assign]
    return bridge, conn


class TestBareContainerNegativeCache:
    """P1-5 (review 2026-08-26): the bare-container guard must not add a
    remote LIST to every plain-dataset query — a 30s negative cache skips
    repeat probes, while container hits keep rejecting every time."""

    def test_plain_dataset_probed_once(self) -> None:
        calls = {"n": 0}

        class _CountingStorage(_FakeStorage):
            def list_container_tables(self, name: str) -> list[str]:
                calls["n"] += 1
                return []

        bridge, _ = _bridge_class(_CountingStorage({"ds": pa.table({"v": [1]})}))
        bridge.query("ds", "SELECT COUNT(*) AS c FROM ds")
        bridge.query("ds", "SELECT COUNT(*) AS c FROM ds")
        assert calls["n"] == 1  # second query served from the negative cache

    def test_container_keeps_rejecting(self) -> None:
        class _ContainerStorage(_FakeStorage):
            def list_container_tables(self, name: str) -> list[str]:
                return ["segments", "valves"]

        from arrow_lake.exceptions import ErrorCode, QueryError

        bridge, _ = _bridge_class(_ContainerStorage({"gas.x": pa.table({"v": [1]})}))
        for _ in range(3):
            with pytest.raises(QueryError) as ei:
                bridge.query("gas", "SELECT COUNT(*) FROM gas")
            assert ei.value.error_code == ErrorCode.OLAP_AMBIGUOUS_DATASET
