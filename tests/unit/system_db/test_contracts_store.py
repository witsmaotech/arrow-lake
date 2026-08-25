"""W2.1 contracts store (V012) — version chain: same-hash skip, change → v2+diff."""

from __future__ import annotations

import pytest

from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.contracts import ContractStore

V1 = """
dataset: gas_net
tables:
  segments:
    columns:
      - name: material
        enum: [PE, steel]
"""

V2 = """
dataset: gas_net
tables:
  segments:
    columns:
      - name: material
        enum: [PE, steel, ductile_iron]
      - name: pressure
        range: [0, 4000]
"""

V3_VERY_DIFFERENT = """
dataset: gas_net
tables:
  valves:
    object_class: 阀门
"""


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


@pytest.fixture
def store(db: SystemDB) -> ContractStore:
    return ContractStore(db)


class TestContractVersionChain:
    def test_first_save_is_version_1_no_diff(self, store: ContractStore) -> None:
        rec = store.save_contract("gas_net", V1)
        assert rec["version"] == 1 and rec["created"] is True
        assert rec["diff"] is None

    def test_same_hash_skipped(self, store: ContractStore) -> None:
        store.save_contract("gas_net", V1)
        rec = store.save_contract("gas_net", V1)
        assert rec["created"] is False and rec["version"] == 1
        assert len(store.list_versions("gas_net")) == 1

    def test_change_creates_v2_with_structured_diff(self, store: ContractStore) -> None:
        store.save_contract("gas_net", V1)
        rec = store.save_contract("gas_net", V2)
        assert rec["created"] is True and rec["version"] == 2
        diff = rec["diff"]
        assert diff is not None
        # material enum widened + pressure range added
        seg = diff["tables"]["segments"]
        assert any(
            c.get("column") == "pressure" and c.get("change") == "added"
            for c in seg["columns"]
        )
        assert any(
            c.get("column") == "material" and c.get("change") == "changed"
            for c in seg["columns"]
        )

    def test_table_added_removed_in_diff(self, store: ContractStore) -> None:
        store.save_contract("gas_net", V1)
        rec = store.save_contract("gas_net", V3_VERY_DIFFERENT)
        diff = rec["diff"]
        assert diff["tables_added"] == ["valves"]
        assert diff["tables_removed"] == ["segments"]

    def test_version_chain_newest_first(self, store: ContractStore) -> None:
        store.save_contract("gas_net", V1)
        store.save_contract("gas_net", V2)
        versions = [v["version"] for v in store.list_versions("gas_net")]
        assert versions == [2, 1]

    def test_get_version_payload(self, store: ContractStore) -> None:
        store.save_contract("gas_net", V1)
        store.save_contract("gas_net", V2)
        latest = store.get_version("gas_net")
        assert latest is not None and latest["version"] == 2
        assert "ductile_iron" in latest["contract_yaml"]
        v1 = store.get_version("gas_net", version=1)
        assert v1 is not None and "ductile_iron" not in v1["contract_yaml"]

    def test_scopes_isolated(self, store: ContractStore) -> None:
        store.save_contract("gas_net", V1)
        store.save_contract("other_ds", V1)
        assert len(store.list_versions("gas_net")) == 1
        assert store.get_version("other_ds")["version"] == 1

    def test_persistence_across_store_instances(self, tmp_path) -> None:
        import pathlib

        db_file = pathlib.Path(tmp_path) / "sys.db"
        db1 = SystemDB(f"file:{db_file}")
        Migrator(db1).run()
        ContractStore(db1).save_contract("gas_net", V1)
        db1.close()
        # new connection sees the committed contract (libSQL commit pitfall)
        db2 = SystemDB(f"file:{db_file}")
        rec = ContractStore(db2).get_version("gas_net")
        db2.close()
        assert rec is not None and rec["version"] == 1
