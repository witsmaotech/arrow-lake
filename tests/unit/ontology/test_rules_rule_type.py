"""W1.4 (v1.11.1/DR15 D-2) — ontology_rules ``rule_type`` 五分类 + ``version``.

五分类 code:validation / computation / derivation / transformation /
risk_control(建模侧 M3 规则分类);``version`` 独立于 draft→active→retired
状态机。V013 裸 ALTER 默认回落:存量/未指定规则 → validation / '1'。
"""

from __future__ import annotations

import pytest

from arrow_lake.system_db import Migrator, SystemDB
from arrow_lake.system_db.stores.ontology import RULE_TYPES, OntologyRulesStore


@pytest.fixture
def store() -> OntologyRulesStore:
    conn = SystemDB(":memory:")
    Migrator(conn).run()
    yield OntologyRulesStore(conn)
    conn.close()


class TestRuleTypeVersion:
    def test_defaults_on_insert(self, store: OntologyRulesStore) -> None:
        store.upsert_rule(
            "GAS.LEAK.R001", scope="gas_pilot",
            condition_expr="浓度 > 20%LEL", conclusion="泄漏预警",
            source_ref="GB/T 50493-2019",
        )
        rule = store.get_rule("GAS.LEAK.R001")
        assert rule["rule_type"] == "validation"
        assert rule["version"] == "1"

    def test_explicit_values_roundtrip(self, store: OntologyRulesStore) -> None:
        store.upsert_rule(
            "GAS.LEAK.R002", scope="gas_pilot",
            condition_expr="压差 > 阈值", conclusion="计算流量",
            source_ref="内控",
            rule_type="computation", version="2.1",
        )
        rule = store.get_rule("GAS.LEAK.R002")
        assert rule["rule_type"] == "computation"
        assert rule["version"] == "2.1"

    def test_all_five_types_accepted(self, store: OntologyRulesStore) -> None:
        assert RULE_TYPES == (
            "validation", "computation", "derivation",
            "transformation", "risk_control",
        )
        for i, rt in enumerate(RULE_TYPES):
            store.upsert_rule(
                f"R.{i}", scope="*", condition_expr="c", conclusion="k",
                source_ref="s", rule_type=rt,
            )
        assert {r["rule_type"] for r in store.list_rules()} == set(RULE_TYPES)

    def test_invalid_rule_type_rejected(self, store: OntologyRulesStore) -> None:
        with pytest.raises(ValueError, match="rule_type"):
            store.upsert_rule(
                "R.BAD", scope="*", condition_expr="c", conclusion="k",
                source_ref="s", rule_type="judgement",
            )

    def test_update_keeps_values_when_omitted(self, store: OntologyRulesStore) -> None:
        store.upsert_rule(
            "GAS.LEAK.R003", scope="gas_pilot",
            condition_expr="c1", conclusion="k1", source_ref="s1",
            rule_type="risk_control", version="3",
        )
        store.upsert_rule(
            "GAS.LEAK.R003", scope="gas_pilot",
            condition_expr="c2", conclusion="k2", source_ref="s2",
        )
        rule = store.get_rule("GAS.LEAK.R003")
        assert rule["condition_expr"] == "c2"
        assert rule["rule_type"] == "risk_control"
        assert rule["version"] == "3"

    def test_explicit_update_overrides(self, store: OntologyRulesStore) -> None:
        store.upsert_rule(
            "GAS.LEAK.R004", scope="*", condition_expr="c", conclusion="k",
            source_ref="s",
        )
        store.upsert_rule(
            "GAS.LEAK.R004", scope="*", condition_expr="c", conclusion="k",
            source_ref="s", rule_type="transformation", version="2",
        )
        rule = store.get_rule("GAS.LEAK.R004")
        assert rule["rule_type"] == "transformation"
        assert rule["version"] == "2"

    def test_transition_preserves_type_and_version(self, store: OntologyRulesStore) -> None:
        store.upsert_rule(
            "GAS.LEAK.R005", scope="*", condition_expr="c", conclusion="k",
            source_ref="s", rule_type="derivation", version="5",
        )
        assert store.transition("GAS.LEAK.R005", "active")
        rule = store.get_rule("GAS.LEAK.R005")
        assert rule["status"] == "active"
        assert rule["rule_type"] == "derivation"
        assert rule["version"] == "5"


class TestListFilter:
    def test_filter_by_rule_type(self, store: OntologyRulesStore) -> None:
        for i, rt in enumerate(RULE_TYPES):
            store.upsert_rule(
                f"F.{i}", scope="*", condition_expr="c", conclusion="k",
                source_ref="s", rule_type=rt,
            )
        risks = store.list_rules(rule_type="risk_control")
        assert [r["rule_id"] for r in risks] == ["F.4"]

    def test_filter_no_match(self, store: OntologyRulesStore) -> None:
        store.upsert_rule(
            "F.X", scope="*", condition_expr="c", conclusion="k", source_ref="s",
        )
        assert store.list_rules(rule_type="transformation") == []
