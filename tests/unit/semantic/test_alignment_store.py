"""W3.2 store — SemanticAlignmentStore:V015 版本链(同 hash 跳过)。

沿 ContractStore 模式但首版无结构化 diff(设计 §4.2 缺口登记);写后显式
commit(libSQL 速查坑)。
"""

from __future__ import annotations

import pytest
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.semantic_alignments import SemanticAlignmentStore

YAML_V1 = """
dataset: gas_net
tables:
  measurements_src_b:
    columns:
      压力: {unit: {from: MPa, to: kPa}}
"""

YAML_V2 = """
dataset: gas_net
tables:
  measurements_src_b:
    columns:
      压力: {unit: {from: MPa, to: kPa}}
      材质: {value_map: {PE管: PE}}
"""


@pytest.fixture
def store() -> SemanticAlignmentStore:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield SemanticAlignmentStore(conn)
    conn.close()


class TestVersionChain:
    def test_first_save_is_version_1(self, store: SemanticAlignmentStore) -> None:
        rec = store.save_alignment("gas_net", YAML_V1)
        assert rec["version"] == 1 and rec["created"] is True

    def test_same_hash_skips(self, store: SemanticAlignmentStore) -> None:
        rec = store.save_alignment("gas_net", YAML_V1)
        rec2 = store.save_alignment("gas_net", YAML_V1)  # identical → skip
        assert rec2["created"] is False
        assert rec2["version"] == rec["version"] == 1
        assert len(store.list_versions("gas_net")) == 1

    def test_change_bumps_version(self, store: SemanticAlignmentStore) -> None:
        store.save_alignment("gas_net", YAML_V1)
        rec = store.save_alignment("gas_net", YAML_V2)
        assert rec["version"] == 2 and rec["created"] is True

    def test_get_specific_version(self, store: SemanticAlignmentStore) -> None:
        store.save_alignment("gas_net", YAML_V1)
        store.save_alignment("gas_net", YAML_V2)
        v1 = store.get_version("gas_net", version=1)
        assert v1 is not None and "材质" not in v1["alignment_yaml"]

    def test_get_unknown_scope_none(self, store: SemanticAlignmentStore) -> None:
        assert store.get_version("nope") is None

    def test_list_scopes_latest(self, store: SemanticAlignmentStore) -> None:
        store.save_alignment("gas_net", YAML_V1)
        store.save_alignment("gas_net", YAML_V2)
        store.save_alignment("other", YAML_V1)
        scopes = {s["scope"]: s["version"] for s in store.list_scopes()}
        assert scopes == {"gas_net": 2, "other": 1}

    def test_delete_scope(self, store: SemanticAlignmentStore) -> None:
        store.save_alignment("gas_net", YAML_V1)
        assert store.delete_scope("gas_net") is True
        assert store.get_version("gas_net") is None
        assert store.delete_scope("gas_net") is False
