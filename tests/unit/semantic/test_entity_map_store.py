"""W2.1 (v1.11.1 F2.1) — EntityMapStore:V014 entity_map(源系统 ID → 对象 ID)。

UNIQUE(scope, table_name, source_system, source_id);冲突 upsert 改写
object_id;写后显式 commit(libSQL 速查坑);显式维护(ADMIN),不挂摄入。
"""

from __future__ import annotations

import pytest

from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.entity_map import EntityMapStore


@pytest.fixture
def store() -> EntityMapStore:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield EntityMapStore(conn)
    conn.close()


def _row(**overrides) -> dict:
    row = {
        "scope": "gas_net", "table_name": "segments",
        "source_system": "SCADA-A", "source_id": "S-047",
        "object_id": "GAS.SEGMENT.RG01-001-S047",
    }
    row.update(overrides)
    return row


class TestUpsertLookup:
    def test_upsert_and_lookup(self, store: EntityMapStore) -> None:
        store.upsert(**_row())
        got = store.lookup(
            scope="gas_net", table_name="segments",
            source_system="SCADA-A", source_id="S-047",
        )
        assert got == "GAS.SEGMENT.RG01-001-S047"

    def test_lookup_miss_returns_none(self, store: EntityMapStore) -> None:
        assert store.lookup(
            scope="gas_net", table_name="segments",
            source_system="SCADA-A", source_id="nope",
        ) is None

    def test_conflict_upsert_rewrites_object_id(self, store: EntityMapStore) -> None:
        store.upsert(**_row())
        store.upsert(**_row(object_id="GAS.SEGMENT.RG01-001-S999"))
        entries = store.list_entries(scope="gas_net")
        assert len(entries) == 1
        assert entries[0]["object_id"] == "GAS.SEGMENT.RG01-001-S999"

    def test_same_source_id_different_system_coexist(self, store: EntityMapStore) -> None:
        store.upsert(**_row())
        store.upsert(**_row(source_system="GIS-B", object_id="GAS.SEGMENT.RG01-001-S048"))
        assert len(store.list_entries(scope="gas_net")) == 2

    def test_same_key_different_table_coexist(self, store: EntityMapStore) -> None:
        store.upsert(**_row())
        store.upsert(**_row(table_name="valves", object_id="GAS.VALVE.X-1"))
        assert len(store.list_entries(scope="gas_net")) == 2


class TestBulk:
    def test_bulk_upsert_insert_and_update(self, store: EntityMapStore) -> None:
        store.upsert(**_row())
        written = store.bulk_upsert([
            _row(object_id="GAS.SEGMENT.RG01-001-S888"),               # update
            _row(source_id="S-048", object_id="GAS.SEGMENT.RG01-001-S048"),  # insert
        ])
        assert written == 2
        entries = store.list_entries(scope="gas_net")
        assert len(entries) == 2

    def test_bulk_idempotent_rerun(self, store: EntityMapStore) -> None:
        rows = [_row(source_id=f"S-{n:03d}", object_id=f"OBJ-{n}") for n in range(5)]
        store.bulk_upsert(rows)
        store.bulk_upsert(rows)
        assert len(store.list_entries(scope="gas_net")) == 5

    def test_bulk_empty_is_noop(self, store: EntityMapStore) -> None:
        assert store.bulk_upsert([]) == 0


class TestListDelete:
    def test_list_filters_by_table(self, store: EntityMapStore) -> None:
        store.upsert(**_row())
        store.upsert(**_row(table_name="valves", source_id="V-1", object_id="OBJ.V1"))
        segs = store.list_entries(scope="gas_net", table_name="segments")
        assert [e["source_id"] for e in segs] == ["S-047"]

    def test_delete_by_key(self, store: EntityMapStore) -> None:
        store.upsert(**_row())
        assert store.delete(
            scope="gas_net", table_name="segments",
            source_system="SCADA-A", source_id="S-047",
        ) is True
        assert store.list_entries(scope="gas_net") == []

    def test_delete_miss_returns_false(self, store: EntityMapStore) -> None:
        assert store.delete(
            scope="x", table_name="y", source_system="z", source_id="w",
        ) is False
