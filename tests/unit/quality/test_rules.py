"""Tests for QualityRuleEngine — declarative quality rules."""

from __future__ import annotations

import json
import os
import tempfile

import pyarrow as pa
import pytest

from arrow_lake.quality.rules import QualityRuleEngine, RuleDefinition, RuleResult


def _make_table(**columns: list) -> pa.Table:
    return pa.table(columns)


class TestRuleDefinition:
    def test_valid_rule(self) -> None:
        rule = RuleDefinition(name="test", column="col", check="length", action="reject")
        assert rule.check == "length"

    def test_invalid_check_raises(self) -> None:
        with pytest.raises(ValueError, match="check must be"):
            RuleDefinition(name="test", column="col", check="invalid")

    def test_invalid_action_raises(self) -> None:
        with pytest.raises(ValueError, match="action must be"):
            RuleDefinition(name="test", column="col", check="length", action="delete")

    def test_frozen(self) -> None:
        rule = RuleDefinition(name="test", column="col", check="length")
        with pytest.raises(AttributeError):
            rule.name = "changed"  # type: ignore[misc]

    def test_default_action_is_flag(self) -> None:
        rule = RuleDefinition(name="test", column="col", check="length")
        assert rule.action == "flag"

    def test_default_params_empty(self) -> None:
        rule = RuleDefinition(name="test", column="col", check="length")
        assert rule.params == {}


class TestLengthCheck:
    def test_reject_short_text(self) -> None:
        table = _make_table(text_content=["hello", "hi", "world", "a"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="reject_short",
            column="text_content",
            check="length",
            params={"min": 3},
            action="reject",
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert results[0].affected_count == 2  # "hi" and "a"

    def test_max_length_violation(self) -> None:
        table = _make_table(text_content=["ok", "this is way too long"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="too_long",
            column="text_content",
            check="length",
            params={"max": 5},
            action="flag",
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert results[0].affected_count == 1

    def test_no_violations(self) -> None:
        table = _make_table(text_content=["hello", "world"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="min_len",
            column="text_content",
            check="length",
            params={"min": 3},
        ))
        results = engine.evaluate(table)
        assert len(results) == 0

    def test_min_and_max_combined(self) -> None:
        table = _make_table(text_content=["a", "ok", "hello", "way too long here"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="len_range",
            column="text_content",
            check="length",
            params={"min": 2, "max": 6},
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert results[0].affected_count == 2  # "a" (1) and "way too long here" (17)


class TestRangeCheck:
    def test_reject_out_of_range(self) -> None:
        table = _make_table(score=[1.0, 5.0, 10.0, 15.0])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="score_range",
            column="score",
            check="range",
            params={"min": 0, "max": 10},
            action="reject",
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert results[0].affected_count == 1  # 15.0

    def test_min_only(self) -> None:
        table = _make_table(value=[5, 3, 10])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="min_val",
            column="value",
            check="range",
            params={"min": 4},
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert results[0].affected_count == 1  # 3

    def test_max_only(self) -> None:
        table = _make_table(value=[1, 5, 10])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="max_val",
            column="value",
            check="range",
            params={"max": 5},
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert results[0].affected_count == 1  # 10

    def test_no_params_no_violations(self) -> None:
        table = _make_table(value=[1, 2, 3])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="empty_range",
            column="value",
            check="range",
        ))
        results = engine.evaluate(table)
        assert len(results) == 0


class TestRegexCheck:
    def test_regex_match(self) -> None:
        table = _make_table(email=["user@test.com", "not-email", "ok@ok.org"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="email_format",
            column="email",
            check="regex",
            params={"pattern": r"^.+@.+$", "invert": True},
            action="reject",
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert results[0].affected_count == 1  # "not-email"

    def test_regex_invert(self) -> None:
        table = _make_table(tag=["good", "bad", "ugly"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="block_bad",
            column="tag",
            check="regex",
            params={"pattern": "bad", "invert": True},
            action="flag",
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert results[0].affected_count == 2  # "good", "ugly"

    def test_empty_pattern_no_match(self) -> None:
        table = _make_table(tag=["a", "b"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="empty_pat",
            column="tag",
            check="regex",
            params={"pattern": ""},
        ))
        results = engine.evaluate(table)
        assert len(results) == 0


class TestDuplicateCheck:
    def test_find_duplicates(self) -> None:
        table = _make_table(text_content=["hello", "world", "hello", "foo"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="dedup",
            column="text_content",
            check="duplicate",
            action="remove",
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert results[0].affected_count == 1  # second "hello"

    def test_no_duplicates(self) -> None:
        table = _make_table(text_content=["a", "b", "c"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="dedup",
            column="text_content",
            check="duplicate",
        ))
        results = engine.evaluate(table)
        assert len(results) == 0

    def test_multiple_duplicates(self) -> None:
        table = _make_table(text_content=["a", "b", "a", "c", "b", "a"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="dedup",
            column="text_content",
            check="duplicate",
            action="remove",
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        # 2nd "a", 2nd "b", 3rd "a" = 3 duplicates
        assert results[0].affected_count == 3


class TestNullHandling:
    def test_null_in_length_check(self) -> None:
        table = _make_table(text_content=["hello", None, "hi"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="min_len",
            column="text_content",
            check="length",
            params={"min": 3},
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert results[0].affected_count == 1  # only "hi", null skipped

    def test_null_in_range_check(self) -> None:
        table = _make_table(score=[5.0, None, 15.0])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="range_check",
            column="score",
            check="range",
            params={"max": 10},
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert results[0].affected_count == 1  # only 15.0

    def test_null_in_regex_check(self) -> None:
        table = _make_table(email=["a@b.com", None, "bad"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="email_check",
            column="email",
            check="regex",
            params={"pattern": r"^.+@.+$"},
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert results[0].affected_count == 1  # only "bad"

    def test_null_in_duplicate_check(self) -> None:
        table = _make_table(text_content=[None, "a", None, "a"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="dedup",
            column="text_content",
            check="duplicate",
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert results[0].affected_count == 1  # 2nd "a", nulls ignored


class TestApply:
    def test_apply_removes_rejected_rows(self) -> None:
        table = _make_table(text_content=["hello", "hi", "world", "a"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="min_len",
            column="text_content",
            check="length",
            params={"min": 3},
            action="reject",
        ))
        filtered, results = engine.apply(table)
        assert filtered.num_rows == 2  # "hello", "world"
        assert len(results) == 1

    def test_apply_multiple_rules_mixed_actions(self) -> None:
        table = _make_table(
            text_content=["ok", "x", "good", "bad"],
            score=[5.0, 1.0, 20.0, 3.0],
        )
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="min_text",
            column="text_content",
            check="length",
            params={"min": 2},
            action="reject",
        ))
        engine.add_rule(RuleDefinition(
            name="low_score",
            column="score",
            check="range",
            params={"min": 2.0},
            action="remove",
        ))
        engine.add_rule(RuleDefinition(
            name="high_score_flag",
            column="score",
            check="range",
            params={"max": 15.0},
            action="flag",
        ))
        filtered, results = engine.apply(table)
        # Row 1 ("x", 1.0) triggers both reject + remove → same row removed once
        # Row 2 ("good", 20.0) flagged only → kept
        # Remaining: ["ok"(5), "good"(20), "bad"(3)]
        assert len(results) == 3
        assert filtered.num_rows == 3

    def test_apply_flag_only_keeps_all_rows(self) -> None:
        table = _make_table(text_content=["ab", "hello"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="short_flag",
            column="text_content",
            check="length",
            params={"min": 3},
            action="flag",
        ))
        filtered, results = engine.apply(table)
        assert filtered.num_rows == 2  # flag doesn't remove
        assert len(results) == 1

    def test_apply_empty_rules(self) -> None:
        table = _make_table(text_content=["a", "b"])
        engine = QualityRuleEngine()
        filtered, results = engine.apply(table)
        assert filtered.num_rows == 2
        assert len(results) == 0

    def test_apply_no_violations(self) -> None:
        table = _make_table(text_content=["hello", "world"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="min_len",
            column="text_content",
            check="length",
            params={"min": 3},
        ))
        filtered, results = engine.apply(table)
        assert filtered.num_rows == 2  # no violations → no removal
        assert len(results) == 0


class TestLoadFromDict:
    def test_load_rules(self) -> None:
        engine = QualityRuleEngine()
        count = engine.load_from_dict({
            "rules": [
                {"name": "r1", "column": "col", "check": "length", "params": {"min": 5}},
                {"name": "r2", "column": "col", "check": "range", "params": {"min": 0}},
            ]
        })
        assert count == 2
        assert len(engine.rules) == 2

    def test_load_empty_rules(self) -> None:
        engine = QualityRuleEngine()
        count = engine.load_from_dict({"rules": []})
        assert count == 0

    def test_load_missing_rules_key(self) -> None:
        engine = QualityRuleEngine()
        count = engine.load_from_dict({})
        assert count == 0

    def test_load_with_defaults(self) -> None:
        engine = QualityRuleEngine()
        engine.load_from_dict({
            "rules": [{"name": "r1", "column": "col", "check": "length"}]
        })
        assert engine.rules[0].action == "flag"
        assert engine.rules[0].params == {}


class TestLoadFromJson:
    def test_load_json_file(self) -> None:
        data = {
            "rules": [
                {"name": "r1", "column": "text", "check": "length", "params": {"min": 5}},
                {"name": "r2", "column": "score", "check": "range", "params": {"max": 100}},
            ]
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            path = f.name
        try:
            engine = QualityRuleEngine()
            count = engine.load_from_json(path)
            assert count == 2
            assert engine.rules[0].name == "r1"
            assert engine.rules[1].name == "r2"
        finally:
            os.unlink(path)


class TestClearAndRules:
    def test_clear(self) -> None:
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(name="r1", column="col", check="length"))
        engine.clear()
        assert len(engine.rules) == 0

    def test_rules_returns_copy(self) -> None:
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(name="r1", column="col", check="length"))
        rules = engine.rules
        rules.clear()
        assert len(engine.rules) == 1  # original unaffected


class TestMissingColumn:
    def test_missing_column_returns_none(self) -> None:
        table = _make_table(text_content=["hello"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="missing",
            column="nonexistent",
            check="length",
        ))
        results = engine.evaluate(table)
        assert len(results) == 0


class TestMessageTemplate:
    def test_custom_message(self) -> None:
        table = _make_table(text_content=["ab"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="min_len",
            column="text_content",
            check="length",
            params={"min": 5},
            message="Text too short (min={min} chars)",
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert "min=5" in results[0].message

    def test_default_message(self) -> None:
        table = _make_table(text_content=["ab"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="min_len",
            column="text_content",
            check="length",
            params={"min": 5},
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        assert "min_len" in results[0].message
        assert "length" in results[0].message

    def test_message_with_bad_template(self) -> None:
        table = _make_table(text_content=["ab"])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="bad_msg",
            column="text_content",
            check="length",
            params={"min": 5},
            message="Missing {nonexistent_key}",
        ))
        results = engine.evaluate(table)
        assert len(results) == 1
        # Should not crash, message kept as-is when formatting fails
        assert "{nonexistent_key}" in results[0].message


class TestEmptyTable:
    def test_evaluate_empty_table(self) -> None:
        table = _make_table(text_content=[])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="r1",
            column="text_content",
            check="length",
            params={"min": 5},
        ))
        results = engine.evaluate(table)
        assert len(results) == 0

    def test_apply_empty_table(self) -> None:
        table = _make_table(text_content=[])
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="r1",
            column="text_content",
            check="length",
            params={"min": 5},
            action="reject",
        ))
        filtered, results = engine.apply(table)
        assert filtered.num_rows == 0
        assert len(results) == 0


class TestMultipleRules:
    def test_multiple_rules_different_columns(self) -> None:
        table = _make_table(
            text_content=["short", "long enough text"],
            score=[5.0, 50.0],
        )
        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="min_text",
            column="text_content",
            check="length",
            params={"min": 10},
            action="reject",
        ))
        engine.add_rule(RuleDefinition(
            name="max_score",
            column="score",
            check="range",
            params={"max": 20},
            action="flag",
        ))
        results = engine.evaluate(table)
        assert len(results) == 2
