"""W1.5 — OntologyRulesStore:V010 ontology_rules 的 CRUD + 状态机(MS1 F1.2)。

状态机:draft → active → retired → draft(循环启用);其余迁移拒绝。
libSQL 写后必须显式 commit(CLAUDE.md 速查坑 — 测试钉住)。
"""

from __future__ import annotations

import pytest

from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.ontology import OntologyRulesStore


@pytest.fixture
def store() -> OntologyRulesStore:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield OntologyRulesStore(conn)
    conn.close()


def _upsert(store: OntologyRulesStore, rule_id: str = "GAS.LEAK.R001") -> None:
    store.upsert_rule(
        rule_id,
        scope="gas_pilot",
        condition_expr="浓度 > 20%LEL AND 持续 > 5min",
        conclusion="触发泄漏预警",
        source_ref="GB/T 50493-2019 §5.2",
    )


class TestUpsertAndRead:
    def test_upsert_and_get(self, store: OntologyRulesStore) -> None:
        _upsert(store)
        rule = store.get_rule("GAS.LEAK.R001")
        assert rule is not None
        assert rule["scope"] == "gas_pilot"
        assert rule["conclusion"] == "触发泄漏预警"
        assert rule["source_ref"] == "GB/T 50493-2019 §5.2"
        assert rule["status"] == "draft"

    def test_upsert_twice_updates_not_duplicates(self, store: OntologyRulesStore) -> None:
        _upsert(store)
        store.upsert_rule(
            "GAS.LEAK.R001", scope="gas_pilot",
            condition_expr="新条件", conclusion="新结论", source_ref="新来源",
        )
        rules = store.list_rules()
        assert len(rules) == 1
        assert rules[0]["conclusion"] == "新结论"

    def test_get_missing_returns_none(self, store: OntologyRulesStore) -> None:
        assert store.get_rule("nope") is None


class TestStateMachine:
    def test_draft_to_active_to_retired(self, store: OntologyRulesStore) -> None:
        _upsert(store)
        assert store.transition("GAS.LEAK.R001", "active")
        assert store.get_rule("GAS.LEAK.R001")["status"] == "active"
        assert store.transition("GAS.LEAK.R001", "retired")
        assert store.get_rule("GAS.LEAK.R001")["status"] == "retired"
        assert store.transition("GAS.LEAK.R001", "draft")  # 复活重用
        assert store.get_rule("GAS.LEAK.R001")["status"] == "draft"

    def test_illegal_transition_raises(self, store: OntologyRulesStore) -> None:
        _upsert(store)
        with pytest.raises(ValueError, match="transition"):
            store.transition("GAS.LEAK.R001", "retired")  # draft → retired 跳级,拒绝

    def test_transition_missing_rule_returns_false(self, store: OntologyRulesStore) -> None:
        assert store.transition("ghost", "active") is False


class TestListAndDelete:
    def test_list_filters(self, store: OntologyRulesStore) -> None:
        _upsert(store)
        store.upsert_rule(
            "GAS.PRESS.R002", scope="gas_pilot",
            condition_expr="c2", conclusion="k2", source_ref="s2",
        )
        assert len(store.list_rules(scope="gas_pilot")) == 2
        store.transition("GAS.LEAK.R001", "active")
        assert len(store.list_rules(status="active")) == 1
        assert store.list_rules(status="active")[0]["rule_id"] == "GAS.LEAK.R001"

    def test_delete(self, store: OntologyRulesStore) -> None:
        _upsert(store)
        assert store.delete_rule("GAS.LEAK.R001") is True
        assert store.get_rule("GAS.LEAK.R001") is None
        assert store.delete_rule("GAS.LEAK.R001") is False


class TestPersistence:
    def test_writes_are_committed(self, store: OntologyRulesStore) -> None:
        """libSQL 不 autocommit — 写方法必须显式 commit(历史踩坑,钉住)。"""
        db = store._db
        committed = [False]

        orig_commit = db.commit

        def spy_commit() -> None:
            committed[0] = True
            orig_commit()

        db.commit = spy_commit  # type: ignore[method-assign]
        _upsert(store)
        assert committed[0], "upsert_rule 必须 commit"
