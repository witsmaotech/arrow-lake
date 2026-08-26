"""W1.2 container registry (DR14) — control-plane container identity.

Container identity is authoritative in the catalog store's
``container_registry`` table (D3), never sniffed from storage directories.
"""

from __future__ import annotations

import pytest

from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores import CatalogStore


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


@pytest.fixture
def catalog(db: SystemDB) -> CatalogStore:
    return CatalogStore(db)


class TestContainerRegistration:
    def test_register_and_is_container(self, catalog: CatalogStore) -> None:
        catalog.register_container("gas_net", ["segments", "stations"])
        assert catalog.is_container("gas_net") is True

    def test_unregistered_is_not_container(self, catalog: CatalogStore) -> None:
        # D3 TDD: a directory existing in storage means nothing — only
        # registration confers container identity.
        assert catalog.is_container("ghost_dir") is False

    def test_get_container_returns_tables(self, catalog: CatalogStore) -> None:
        catalog.register_container("gas_net", ["stations", "segments"])
        info = catalog.get_container("gas_net")
        assert info is not None
        assert info["tables"] == ["segments", "stations"]  # sorted + deduped
        assert info["created_at"]

    def test_get_missing_container_returns_none(self, catalog: CatalogStore) -> None:
        assert catalog.get_container("nope") is None

    def test_reregister_replaces_table_list(self, catalog: CatalogStore) -> None:
        catalog.register_container("gas_net", ["segments"])
        catalog.register_container("gas_net", ["segments", "valves"])
        assert catalog.get_container("gas_net")["tables"] == ["segments", "valves"]

    def test_register_empty_tables(self, catalog: CatalogStore) -> None:
        catalog.register_container("empty_container")
        assert catalog.is_container("empty_container") is True
        assert catalog.get_container("empty_container")["tables"] == []


class TestContainerTableMembership:
    def test_add_table_idempotent(self, catalog: CatalogStore) -> None:
        catalog.register_container("gas_net", ["segments"])
        catalog.add_container_table("gas_net", "stations")
        catalog.add_container_table("gas_net", "stations")  # no dup
        assert catalog.get_container("gas_net")["tables"] == ["segments", "stations"]

    def test_add_table_to_unregistered_registers(self, catalog: CatalogStore) -> None:
        catalog.add_container_table("fresh", "t1")
        assert catalog.is_container("fresh") is True
        assert catalog.get_container("fresh")["tables"] == ["t1"]

    def test_drop_table(self, catalog: CatalogStore) -> None:
        catalog.register_container("gas_net", ["segments", "stations"])
        catalog.drop_container_table("gas_net", "segments")
        assert catalog.get_container("gas_net")["tables"] == ["stations"]

    def test_drop_absent_table_noop(self, catalog: CatalogStore) -> None:
        catalog.register_container("gas_net", ["segments"])
        catalog.drop_container_table("gas_net", "ghost")
        catalog.drop_container_table("ghost_ds", "x")
        assert catalog.get_container("gas_net")["tables"] == ["segments"]

    def test_set_tables_reconcile(self, catalog: CatalogStore) -> None:
        catalog.register_container("gas_net", ["old_shape"])
        catalog.set_container_tables("gas_net", ["a", "b"])
        assert catalog.get_container("gas_net")["tables"] == ["a", "b"]

    def test_set_tables_unregistered_noop(self, catalog: CatalogStore) -> None:
        catalog.set_container_tables("ghost", ["a"])
        assert catalog.is_container("ghost") is False


class TestContainerLifecycle:
    def test_unregister(self, catalog: CatalogStore) -> None:
        catalog.register_container("gas_net", ["segments"])
        assert catalog.unregister_container("gas_net") is True
        assert catalog.is_container("gas_net") is False
        assert catalog.unregister_container("gas_net") is False

    def test_list_containers(self, catalog: CatalogStore) -> None:
        catalog.register_container("b_net", ["t"])
        catalog.register_container("a_net", ["t1", "t2"])
        names = [c["dataset"] for c in catalog.list_containers()]
        assert names == ["a_net", "b_net"]

    def test_coexists_with_dataset_registry(self, catalog: CatalogStore) -> None:
        # Plain datasets and containers share the store, not the namespace.
        catalog.register_table("plain", schema_json="{}", location="/x/plain.lance")
        catalog.register_container("cont", ["t"])
        assert catalog.get_table("plain") is not None
        assert catalog.get_table("cont") is None
        assert catalog.is_container("plain") is False

    def test_persistence_across_store_instances(self, db: SystemDB) -> None:
        CatalogStore(db).register_container("gas_net", ["segments"])
        fresh = CatalogStore(db)
        assert fresh.is_container("gas_net") is True
        assert fresh.get_container("gas_net")["tables"] == ["segments"]


class TestAddContainerTableAtomic:
    """P1-4 (review 2026-08-26, D8): the table merge must survive concurrent
    ingest — the old get→append→set read-modify-write lost a registration
    when two writers raced (cross-worker control-plane identity drift)."""

    def test_merge_preserves_existing_tables(self, catalog: CatalogStore) -> None:
        catalog.register_container("gas_net", ["segments", "valves"])
        catalog.add_container_table("gas_net", "stations")
        assert catalog.get_container("gas_net")["tables"] == [
            "segments", "stations", "valves",
        ]

    def test_concurrent_adds_all_land(self, catalog: CatalogStore) -> None:
        from concurrent.futures import ThreadPoolExecutor

        def _add(i: int) -> None:
            catalog.add_container_table("race", f"t{i:02d}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_add, range(16)))
        tables = catalog.get_container("race")["tables"]
        assert tables == sorted(f"t{i:02d}" for i in range(16))

    def test_merge_on_existing_json(self, catalog: CatalogStore) -> None:
        # second add through the atomic path merges with the first's payload
        catalog.add_container_table("gas_net", "alpha")
        catalog.add_container_table("gas_net", "beta")
        catalog.add_container_table("gas_net", "alpha")  # dedup via DISTINCT
        assert catalog.get_container("gas_net")["tables"] == ["alpha", "beta"]
