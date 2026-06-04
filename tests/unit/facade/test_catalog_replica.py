"""Tests for Story 6.11 — Catalog Read Replica."""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.catalog.replica import CatalogReadReplica
from arrow_lake.exceptions import CatalogError


class TestCatalogReadReplica:
    """Test CatalogReadReplica failover behavior."""

    def test_initial_state(self) -> None:
        replica = CatalogReadReplica()
        assert replica.primary_available is True
        assert replica.is_cache_empty()

    def test_sync_from_actor(self) -> None:
        replica = CatalogReadReplica()
        handle = MagicMock()
        tables = [
            {"name": "users", "schema_json": "{}", "location": "./data/lake", "status": "active"},
            {"name": "docs", "schema_json": "{}", "location": "./data/lake", "status": "active"},
        ]
        fake_ray = types.ModuleType("ray")
        fake_ray.get = MagicMock(return_value=tables)
        with patch.dict("sys.modules", {"ray": fake_ray}):
            count = replica.sync_from_actor(handle)
        assert count == 2
        assert replica.primary_available is True
        assert len(replica.list_tables()) == 2

    def test_sync_from_actor_failure_triggers_failover(self) -> None:
        replica = CatalogReadReplica()
        handle = MagicMock()
        fake_ray = types.ModuleType("ray")
        fake_ray.get = MagicMock(side_effect=ConnectionError("ray down"))
        with (
            patch.dict("sys.modules", {"ray": fake_ray}),
            pytest.raises(CatalogError, match="CATALOG_CONNECTION_FAILED"),
        ):
            replica.sync_from_actor(handle)
        assert replica.primary_available is False

    def test_list_tables_from_cache(self) -> None:
        replica = CatalogReadReplica()
        replica._tables_cache = {"users": {"name": "users"}}
        tables = replica.list_tables()
        assert len(tables) == 1
        assert tables[0]["name"] == "users"

    def test_get_table_from_cache(self) -> None:
        replica = CatalogReadReplica()
        replica._tables_cache = {"users": {"name": "users", "schema": "{}"}}
        result = replica.get_table("users")
        assert result["name"] == "users"

    def test_get_table_not_in_cache(self) -> None:
        replica = CatalogReadReplica()
        with pytest.raises(CatalogError, match="not found in replica"):
            replica.get_table("missing")

    def test_register_table_requires_primary(self) -> None:
        replica = CatalogReadReplica()
        replica.mark_primary_unavailable()
        with pytest.raises(CatalogError, match="Cannot register_table"):
            replica.register_table("new", "{}", "./data")

    def test_delete_table_requires_primary(self) -> None:
        replica = CatalogReadReplica()
        replica.mark_primary_unavailable()
        with pytest.raises(CatalogError, match="Cannot delete_table"):
            replica.delete_table("users")

    def test_mark_primary_unavailable(self) -> None:
        replica = CatalogReadReplica()
        replica.mark_primary_unavailable()
        assert replica.primary_available is False
        with pytest.raises(CatalogError, match="Cannot register_table"):
            replica.register_table("t", "{}", "/")

    def test_mark_primary_available(self) -> None:
        replica = CatalogReadReplica()
        replica.mark_primary_unavailable()
        assert replica.primary_available is False
        replica.mark_primary_available()
        assert replica.primary_available is True

    def test_list_tables_when_primary_down(self) -> None:
        replica = CatalogReadReplica()
        replica._tables_cache = {"users": {"name": "users"}}
        replica.mark_primary_unavailable()
        # Read operations still work from cache
        assert len(replica.list_tables()) == 1

    def test_empty_cache(self) -> None:
        replica = CatalogReadReplica()
        assert replica.list_tables() == []
        assert replica.is_cache_empty()

    def test_register_table_returns_empty_dict_when_primary_available(self) -> None:
        """Cover L130: return {} — unreachable normally, reached when primary is available."""
        replica = CatalogReadReplica()
        assert replica.primary_available is True
        result = replica.register_table("new_table", '{"col": "int"}', "./data")
        assert result == {}

    def test_delete_table_returns_false_when_primary_available(self) -> None:
        """Cover L139: return False — unreachable normally, reached when primary is available."""
        replica = CatalogReadReplica()
        assert replica.primary_available is True
        result = replica.delete_table("some_table")
        assert result is False
