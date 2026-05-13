"""Tests for arrow_lake.catalog.actor — CatalogActor without Ray runtime.

Uses __ray_actor_class__ to access the underlying undecorated class,
then instantiates directly with real DuckDB — no Ray init required.
"""

from __future__ import annotations

import shutil

import pytest

from arrow_lake.catalog.actor import CatalogActor as _RayWrapped
from arrow_lake.exceptions import CatalogError, ErrorCode

# Access the original class before @ray.remote wrapping
_CatalogActor = _RayWrapped.__ray_actor_class__

_instances: list = []


@pytest.fixture()
def actor():
    """Create a real CatalogActor with DuckDB, no Ray runtime."""
    inst = _CatalogActor(max_pool_size=2)
    _instances.append(inst)
    yield inst


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for inst in _instances:
        if hasattr(inst, "_db_dir"):
            shutil.rmtree(inst._db_dir, ignore_errors=True)
    _instances.clear()


class TestCatalogActorHealth:
    def test_ping(self, actor):
        assert actor.ping() is True

    def test_health_returns_pool_info(self, actor):
        h = actor.health()
        assert h["status"] == "healthy"
        assert "pool_size" in h
        assert "active_connections" in h


class TestCatalogActorRegister:
    def test_register_and_get(self, actor):
        result = actor.register_table("test_ds", '{"fields": []}', "/data/test.lance")
        assert result["name"] == "test_ds"
        assert result["location"] == "/data/test.lance"

        meta = actor.get_table("test_ds")
        assert meta["name"] == "test_ds"
        assert meta["status"] == "active"

    def test_register_duplicate_raises(self, actor):
        actor.register_table("dup_ds", '{}', "/data/dup")
        with pytest.raises(CatalogError) as exc_info:
            actor.register_table("dup_ds", '{}', "/data/dup")
        assert exc_info.value.error_code == ErrorCode.CATALOG_DATASET_ALREADY_EXISTS

    def test_register_invalid_name(self, actor):
        with pytest.raises(CatalogError) as exc_info:
            actor.register_table("bad name!", '{}', "/data/bad")
        assert exc_info.value.error_code == ErrorCode.VALIDATION_INVALID_CONFIG


class TestCatalogActorGet:
    def test_get_nonexistent(self, actor):
        with pytest.raises(CatalogError) as exc_info:
            actor.get_table("no_such_table")
        assert exc_info.value.error_code == ErrorCode.CATALOG_DATASET_NOT_FOUND

    def test_get_invalid_name(self, actor):
        with pytest.raises(CatalogError) as exc_info:
            actor.get_table("invalid!!")
        assert exc_info.value.error_code == ErrorCode.VALIDATION_INVALID_CONFIG


class TestCatalogActorList:
    def test_list_empty(self, actor):
        tables = actor.list_tables()
        assert tables == []

    def test_list_returns_registered(self, actor):
        actor.register_table("ds_a", '{}', "/a")
        actor.register_table("ds_b", '{}', "/b")
        tables = actor.list_tables()
        names = [t["name"] for t in tables]
        assert "ds_a" in names
        assert "ds_b" in names


class TestCatalogActorDelete:
    def test_delete_existing(self, actor):
        actor.register_table("to_del", '{}', "/del")
        assert actor.delete_table("to_del") is True
        with pytest.raises(CatalogError):
            actor.get_table("to_del")

    def test_delete_nonexistent(self, actor):
        with pytest.raises(CatalogError):
            actor.delete_table("ghost")

    def test_delete_cascade_without_base_uri_raises(self, actor):
        actor.register_table("cascade_ds", '{}', "/cascade")
        with pytest.raises(CatalogError) as exc_info:
            actor.delete_table("cascade_ds", cascade=True, base_uri=None)
        assert exc_info.value.error_code == ErrorCode.VALIDATION_MISSING_FIELD

    def test_delete_invalid_name(self, actor):
        with pytest.raises(CatalogError) as exc_info:
            actor.delete_table("!!!")
        assert exc_info.value.error_code == ErrorCode.VALIDATION_INVALID_CONFIG


class TestCatalogActorArchiveRestore:
    def test_archive_dataset(self, actor):
        actor.register_table("arch_ds", '{}', "/arch")
        assert actor.archive_dataset("arch_ds") is True

    def test_archive_already_archived(self, actor):
        actor.register_table("arch2", '{}', "/arch2")
        actor.archive_dataset("arch2")
        with pytest.raises(CatalogError) as exc_info:
            actor.archive_dataset("arch2")
        assert exc_info.value.error_code == ErrorCode.CATALOG_DATASET_ALREADY_EXISTS

    def test_restore_dataset(self, actor):
        actor.register_table("rest_ds", '{}', "/rest")
        actor.archive_dataset("rest_ds")
        assert actor.restore_dataset("rest_ds") is True
        meta = actor.get_table("rest_ds")
        assert meta["status"] == "active"

    def test_restore_nonexistent(self, actor):
        with pytest.raises(CatalogError):
            actor.restore_dataset("no_table")

    def test_archive_excludes_from_list(self, actor):
        actor.register_table("vis", '{}', "/vis")
        actor.register_table("hid", '{}', "/hid")
        actor.archive_dataset("hid")
        names = [t["name"] for t in actor.list_tables()]
        assert "vis" in names
        assert "hid" not in names


class TestCatalogActorSql:
    def test_execute_select(self, actor):
        actor.register_table("sql_ds", '{}', "/sql")
        rows = actor.execute_sql("SELECT count(*) FROM catalog_tables")
        assert len(rows) == 1
        assert rows[0][0] == 1

    def test_execute_non_select_rejected(self, actor):
        with pytest.raises(CatalogError) as exc_info:
            actor.execute_sql("DROP TABLE catalog_tables")
        assert exc_info.value.error_code == ErrorCode.QUERY_SYNTAX_ERROR

    def test_execute_select_case_insensitive(self, actor):
        rows = actor.execute_sql("select 1")
        assert rows[0][0] == 1


class TestValidateTableName:
    def test_valid_names(self):
        from arrow_lake.catalog.actor import _validate_table_name
        for name in ["my_table", "Table1", "_private", "has-dash", "CamelCase99"]:
            _validate_table_name(name)

    def test_invalid_names(self):
        from arrow_lake.catalog.actor import _validate_table_name
        for bad in ["", "has space", "a!b", "9start", "dot.name"]:
            with pytest.raises(CatalogError):
                _validate_table_name(bad)

    def test_clear_all(self, actor):
        actor.register_table("a", '{}', "/a")
        actor.register_table("b", '{}', "/b")
        actor._clear_all()
        assert actor.list_tables() == []
