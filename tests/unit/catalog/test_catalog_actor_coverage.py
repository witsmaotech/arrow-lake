"""Cover missing lines in arrow_lake.catalog.actor."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.exceptions import CatalogError
from arrow_lake.catalog.actor import CatalogActor, _validate_table_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Get the real CatalogActor class (unwrapped from @ray.remote)
_ACTOR_CLS = CatalogActor
for _cls in type(CatalogActor).__mro__:
    if _cls.__name__ == "CatalogActor" and _cls is not CatalogActor:
        _ACTOR_CLS = _cls
        break


def _actor(**kw: object) -> _ACTOR_CLS:
    """Create a CatalogActor with mocked DuckDB pool."""
    with patch("arrow_lake.catalog.actor.ray"):
        with patch("arrow_lake.catalog.actor.DuckDBConnectionPool") as mock_pool_cls:
            mock_pool = MagicMock()
            mock_pool_cls.return_value = mock_pool
            with patch.object(_ACTOR_CLS, "_init_schema"):
                a = _ACTOR_CLS(
                    max_pool_size=kw.get("max_pool_size", 5),
                    gravitino_config=kw.get("gravitino_config"),
                )
    return a


# ---------------------------------------------------------------------------
# _validate_table_name
# ---------------------------------------------------------------------------


class TestValidateTableName:
    def test_valid(self) -> None:
        _validate_table_name("my_table")

    def test_invalid_empty(self) -> None:
        with pytest.raises(CatalogError):
            _validate_table_name("")

    def test_invalid_special(self) -> None:
        with pytest.raises(CatalogError):
            _validate_table_name("bad;name")


# ---------------------------------------------------------------------------
# _init_schema
# ---------------------------------------------------------------------------


class TestInitSchema:
    def test_creates_tables(self) -> None:
        with patch("arrow_lake.catalog.actor.ray"):
            with patch("arrow_lake.catalog.actor.DuckDBConnectionPool") as mock_pool_cls:
                mock_pool = MagicMock()
                mock_pool_cls.return_value = mock_pool
                a = _ACTOR_CLS()
                # _init_schema was called in __init__
                assert mock_pool.execute.call_count >= 2


# ---------------------------------------------------------------------------
# ping / health
# ---------------------------------------------------------------------------


class TestPingHealth:
    def test_ping(self) -> None:
        a = _actor()
        assert a.ping() is True

    def test_health(self) -> None:
        a = _actor()
        # health() calls self._pool.health() -> PoolHealth, self._pool.is_closed() -> bool
        mock_health = MagicMock()
        mock_health.pool_size = 5
        mock_health.active_connections = 1
        mock_health.idle_connections = 4
        a._pool.health.return_value = mock_health
        a._pool.is_closed.return_value = False
        result = a.health()
        assert result["status"] == "healthy"

    def test_health_no_bridge(self) -> None:
        a = _actor()
        mock_health = MagicMock()
        mock_health.pool_size = 5
        mock_health.active_connections = 0
        mock_health.idle_connections = 5
        a._pool.health.return_value = mock_health
        a._pool.is_closed.return_value = False
        result = a.health()
        assert "pool_size" in result


# ---------------------------------------------------------------------------
# register_table
# ---------------------------------------------------------------------------


class TestRegisterTable:
    def test_register_success(self) -> None:
        a = _actor()
        # execute_params returns empty list (no existing row)
        a._pool.execute_params.return_value = []
        result = a.register_table("test_tbl", '{"col":"int"}', 'file:///data')
        assert "name" in result

    def test_register_already_exists(self) -> None:
        a = _actor()
        import duckdb
        a._pool.execute_params.side_effect = duckdb.Error(
            "Constraint Error: duplicate key value violates unique constraint"
        )
        with pytest.raises(CatalogError):
            a.register_table("test_tbl", '{}', '/data')

    def test_register_with_gravitino(self) -> None:
        a = _actor(gravitino_config=MagicMock(enabled=True))
        a._pool.execute_params.return_value = []
        with patch.object(a, "_register_gravitino"):
            result = a.register_table("tbl", '{}', '/data')
        assert "name" in result

    def test_register_gravitino(self) -> None:
        a = _actor(gravitino_config=MagicMock(enabled=True))
        a._gravitino_bridge = MagicMock()
        a._register_gravitino("tbl", '{}', '/data')


# ---------------------------------------------------------------------------
# get_table
# ---------------------------------------------------------------------------


class TestGetTable:
    def test_found(self) -> None:
        a = _actor()
        # execute_params returns a row: (name, schema_json, location, created_at, updated_at, status)
        a._pool.execute_params.return_value = [
            ("tbl", '{"col":"int"}', '/data', '2024-01-01', '2024-01-01', 'active'),
        ]
        result = a.get_table("tbl")
        assert result["name"] == "tbl"

    def test_not_found(self) -> None:
        a = _actor()
        # execute_params returns empty list
        a._pool.execute_params.return_value = []
        with pytest.raises(CatalogError):
            a.get_table("missing")


# ---------------------------------------------------------------------------
# list_tables
# ---------------------------------------------------------------------------


class TestListTables:
    def test_list(self) -> None:
        a = _actor()
        # execute() returns list of tuples directly (pool.execute calls fetchall internally)
        a._pool.execute.return_value = [
            ("tbl1", '{}', '/d1', '2024-01-01', '2024-01-01', 'active'),
            ("tbl2", '{}', '/d2', '2024-01-02', '2024-01-02', 'active'),
        ]
        result = a.list_tables()
        assert len(result) == 2


# ---------------------------------------------------------------------------
# delete_table
# ---------------------------------------------------------------------------


class TestDeleteTable:
    def test_delete_success(self) -> None:
        a = _actor()
        # get_table uses execute_params -> returns a row
        a._pool.execute_params.side_effect = [
            [("tbl", '{}', '/data', '2024-01-01', '2024-01-01', 'active')],  # get_table lookup
            [],  # delete execute_params
        ]
        result = a.delete_table("tbl")
        assert result is True

    def test_delete_cascade(self) -> None:
        a = _actor()
        a._pool.execute_params.side_effect = [
            [("tbl", '{}', '/data', '2024-01-01', '2024-01-01', 'active')],  # get_table
            [],  # delete
        ]
        with patch.object(a, "_delete_lance_data"):
            result = a.delete_table("tbl", cascade=True, base_uri="/data")
        assert result is True

    def test_delete_not_found(self) -> None:
        a = _actor()
        # get_table returns empty -> raises CatalogError -> delete_table returns False? No, it raises.
        a._pool.execute_params.return_value = []
        with pytest.raises(CatalogError):
            a.delete_table("missing")


# ---------------------------------------------------------------------------
# archive / restore
# ---------------------------------------------------------------------------


class TestArchiveRestore:
    def test_archive(self) -> None:
        a = _actor()
        a._pool.execute_params.side_effect = [
            [("tbl", '{}', '/data', '2024-01-01', '2024-01-01', 'active')],  # get_table
            [],  # update status
        ]
        result = a.archive_dataset("tbl")
        assert result is True

    def test_archive_not_found(self) -> None:
        a = _actor()
        a._pool.execute_params.return_value = []
        with pytest.raises(CatalogError):
            a.archive_dataset("missing")

    def test_restore(self) -> None:
        a = _actor()
        a._pool.execute_params.side_effect = [
            [("tbl", '{}', '/data', '2024-01-01', '2024-01-01', 'archived')],  # get_table
            [],  # update status
        ]
        result = a.restore_dataset("tbl")
        assert result is True

    def test_restore_not_found(self) -> None:
        a = _actor()
        a._pool.execute_params.return_value = []
        with pytest.raises(CatalogError):
            a.restore_dataset("missing")


# ---------------------------------------------------------------------------
# execute_sql
# ---------------------------------------------------------------------------


class TestExecuteSql:
    def test_success(self) -> None:
        a = _actor()
        # execute() returns list of tuples directly
        a._pool.execute.return_value = [(1, "a"), (2, "b")]
        result = a.execute_sql("SELECT * FROM catalog")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _delete_lance_data
# ---------------------------------------------------------------------------


class TestDeleteLanceData:
    def test_success(self) -> None:
        a = _actor()
        with patch("pathlib.Path.exists", return_value=True), \
             patch("shutil.rmtree"):
            a._delete_lance_data("/data", "tbl")

    def test_not_exists(self) -> None:
        a = _actor()
        with patch("pathlib.Path.exists", return_value=False):
            a._delete_lance_data("/data", "tbl")


# ---------------------------------------------------------------------------
# _clear_all
# ---------------------------------------------------------------------------


class TestClearAll:
    def test_clear(self) -> None:
        a = _actor()
        a._clear_all()
        a._pool.execute.assert_called()
