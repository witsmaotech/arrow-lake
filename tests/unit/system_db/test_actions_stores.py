"""W2.1-2.2 (v1.11.2 MS3) — V016/V017 stores:行动目录版本链+幂等去重+场景版本链。

沿 ContractStore/SemanticAlignmentStore 模式(版本链同 source_hash 跳过,
无结构化 diff——S5 缺口登记)。幂等:owner token 判定竞争胜负(UNIQUE
裁决插入,owner 比对归属),failed 态允许重认领,completed 态重放不重执行。
"""

from __future__ import annotations

import pytest
from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.actions import ActionCatalogStore, IdempotencyStore
from arrow_lake.system_db.stores.scenarios import ScenarioStore

ACTION_YAML_V1 = """
action_id: GAS.ALERT.PUBLISH
title: 发布燃气泄漏预警
target: {dataset: gas_network, object_class: 告警事件}
effect: {type: update_lifecycle, to_state: 已发布}
"""

ACTION_YAML_V2 = ACTION_YAML_V1.replace(
    "to_state: 已发布", "to_state: 已发布\n  fields: {level: '{{ assess.level }}'}"
)

SCENARIO_YAML_V1 = """
scenario_id: GAS.LEAK.RESPONSE
title: 燃气泄漏告警响应
steps:
  - id: publish
    action: GAS.ALERT.PUBLISH
"""


@pytest.fixture
def db() -> SystemDB:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield conn
    conn.close()


class TestActionCatalogStore:
    def test_save_creates_v1(self, db: SystemDB) -> None:
        store = ActionCatalogStore(db)
        rec = store.save_action("GAS.ALERT.PUBLISH", ACTION_YAML_V1)
        assert rec["version"] == 1 and rec["created"] is True

    def test_same_hash_skips_version(self, db: SystemDB) -> None:
        store = ActionCatalogStore(db)
        store.save_action("GAS.ALERT.PUBLISH", ACTION_YAML_V1)
        rec = store.save_action("GAS.ALERT.PUBLISH", ACTION_YAML_V1)
        assert rec["created"] is False and rec["version"] == 1
        assert len(store.list_versions("GAS.ALERT.PUBLISH")) == 1

    def test_change_creates_v2(self, db: SystemDB) -> None:
        store = ActionCatalogStore(db)
        store.save_action("GAS.ALERT.PUBLISH", ACTION_YAML_V1)
        rec = store.save_action("GAS.ALERT.PUBLISH", ACTION_YAML_V2)
        assert rec["version"] == 2 and rec["created"] is True
        got = store.get_version("GAS.ALERT.PUBLISH")
        assert got is not None and "fields" in got["action_yaml"]

    def test_get_specific_version(self, db: SystemDB) -> None:
        store = ActionCatalogStore(db)
        store.save_action("GAS.ALERT.PUBLISH", ACTION_YAML_V1)
        store.save_action("GAS.ALERT.PUBLISH", ACTION_YAML_V2)
        v1 = store.get_version("GAS.ALERT.PUBLISH", version=1)
        assert v1 is not None and "fields" not in v1["action_yaml"]

    def test_list_scopes_latest_only(self, db: SystemDB) -> None:
        store = ActionCatalogStore(db)
        store.save_action("GAS.ALERT.PUBLISH", ACTION_YAML_V1)
        store.save_action("GAS.ALERT.PUBLISH", ACTION_YAML_V2)
        store.save_action(
            "GAS.ALERT.ESCALATE", ACTION_YAML_V1.replace("GAS.ALERT.PUBLISH", "GAS.ALERT.ESCALATE")
        )
        scopes = store.list_scopes()
        assert {s["scope"] for s in scopes} == {"GAS.ALERT.PUBLISH", "GAS.ALERT.ESCALATE"}
        pub = next(s for s in scopes if s["scope"] == "GAS.ALERT.PUBLISH")
        assert pub["version"] == 2

    def test_delete_scope(self, db: SystemDB) -> None:
        store = ActionCatalogStore(db)
        store.save_action("GAS.ALERT.PUBLISH", ACTION_YAML_V1)
        assert store.delete_scope("GAS.ALERT.PUBLISH") is True
        assert store.get_version("GAS.ALERT.PUBLISH") is None
        assert store.delete_scope("GAS.ALERT.PUBLISH") is False


class TestIdempotencyStore:
    def test_first_acquire_wins(self, db: SystemDB) -> None:
        store = IdempotencyStore(db)
        r = store.try_acquire("GAS.ALERT.PUBLISH", "AL-001", owner="worker-a")
        assert r["acquired"] is True and r["state"] == "running"

    def test_competing_acquire_rejected(self, db: SystemDB) -> None:
        store = IdempotencyStore(db)
        store.try_acquire("GAS.ALERT.PUBLISH", "AL-001", owner="worker-a")
        r = store.try_acquire("GAS.ALERT.PUBLISH", "AL-001", owner="worker-b")
        assert r["acquired"] is False
        assert r["state"] == "running" and r["owner"] == "worker-a"

    def test_completed_replay_not_acquired(self, db: SystemDB) -> None:
        store = IdempotencyStore(db)
        store.try_acquire("GAS.ALERT.PUBLISH", "AL-001", owner="worker-a")
        assert store.mark("GAS.ALERT.PUBLISH", "AL-001", "completed") is True
        r = store.try_acquire("GAS.ALERT.PUBLISH", "AL-001", owner="worker-b")
        assert r["acquired"] is False and r["state"] == "completed"

    def test_failed_can_be_reclaimed(self, db: SystemDB) -> None:
        store = IdempotencyStore(db)
        store.try_acquire("GAS.ALERT.PUBLISH", "AL-001", owner="worker-a")
        store.mark("GAS.ALERT.PUBLISH", "AL-001", "failed", detail="dead-letter")
        r = store.try_acquire("GAS.ALERT.PUBLISH", "AL-001", owner="worker-b")
        assert r["acquired"] is True and r["owner"] == "worker-b"

    def test_keys_are_per_action_scoped(self, db: SystemDB) -> None:
        store = IdempotencyStore(db)
        store.try_acquire("GAS.ALERT.PUBLISH", "AL-001", owner="a")
        r = store.try_acquire("GAS.ALERT.ESCALATE", "AL-001", owner="b")
        assert r["acquired"] is True

    def test_get_missing_none(self, db: SystemDB) -> None:
        assert IdempotencyStore(db).get("X", "Y") is None


class TestScenarioStore:
    def test_version_chain_skip_and_bump(self, db: SystemDB) -> None:
        store = ScenarioStore(db)
        rec1 = store.save_scenario("GAS.LEAK.RESPONSE", SCENARIO_YAML_V1)
        assert rec1["version"] == 1 and rec1["created"] is True
        rec2 = store.save_scenario("GAS.LEAK.RESPONSE", SCENARIO_YAML_V1)
        assert rec2["created"] is False
        v2_yaml = SCENARIO_YAML_V1 + "  - id: withdraw\n    action: GAS.ALERT.WITHDRAW\n"
        rec3 = store.save_scenario("GAS.LEAK.RESPONSE", v2_yaml)
        assert rec3["version"] == 2 and rec3["created"] is True
        assert [v["version"] for v in store.list_versions("GAS.LEAK.RESPONSE")] == [2, 1]

    def test_get_and_delete(self, db: SystemDB) -> None:
        store = ScenarioStore(db)
        store.save_scenario("GAS.LEAK.RESPONSE", SCENARIO_YAML_V1)
        got = store.get_version("GAS.LEAK.RESPONSE")
        assert got is not None and "GAS.LEAK.RESPONSE" in got["scenario_yaml"]
        assert store.delete_scope("GAS.LEAK.RESPONSE") is True
        assert store.get_version("GAS.LEAK.RESPONSE") is None
