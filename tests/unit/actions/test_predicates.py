"""W1.1 (v1.11.2 MS3 F3.1/F3.3) — 谓词表达式 DSL:tokenizer/parser/AST/evaluator。

规范:docs_offline/ms3-modeling-language-design.md §4。纯手写解析器(无
eval/exec/函数调用);表达式长度帽 512;path 段字符集白名单(允许中文,
与契约列名规则一致);求值上下文缺失 path → None 语义(任何比较 → False
不炸,fail-safe 不断言)。与 ontology_rules.condition_expr 同源(W3.2)。
"""

from __future__ import annotations

import pytest
from arrow_lake.actions.predicates import (
    ParsedPredicateError,
    compile_predicate,
    evaluate_expr,
)


def _ctx() -> dict:
    return {
        "研判结论": "预警",
        "target": {
            "lifecycle_state": "待研判",
            "object_id": "AL-001",
            "压力": 0.45,
            "风险等级": "高",
            "tags": ["一类", "三类"],
            "active": True,
        },
        "assess": {"confidence": 0.9, "matched_rules": 2, "level": "橙色"},
        "actor": {"sub": "u1", "role": "sysop"},
    }


class TestComparison:
    def test_string_equality(self) -> None:
        assert evaluate_expr("target.lifecycle_state == '待研判'", _ctx())
        assert not evaluate_expr("target.lifecycle_state == '已发布'", _ctx())

    def test_numeric_ordering(self) -> None:
        assert evaluate_expr("assess.confidence >= 0.8", _ctx())
        assert not evaluate_expr("assess.confidence > 0.95", _ctx())
        assert evaluate_expr("assess.confidence < 1", _ctx())

    def test_not_equal(self) -> None:
        assert evaluate_expr("target.lifecycle_state != '已发布'", _ctx())

    def test_negative_number_literal(self) -> None:
        assert not evaluate_expr("target.压力 < -1", _ctx())
        assert evaluate_expr("target.压力 > -1", _ctx())


class TestLogic:
    def test_and(self) -> None:
        expr = "assess.confidence >= 0.8 && actor.role == 'sysop'"
        assert evaluate_expr(expr, _ctx())
        assert not evaluate_expr(expr + " && assess.matched_rules == 0", _ctx())

    def test_or(self) -> None:
        expr = "actor.role == 'viewer' || actor.role == 'sysop'"
        assert evaluate_expr(expr, _ctx())

    def test_keyword_case_insensitive_forms(self) -> None:
        assert evaluate_expr("assess.confidence >= 0.8 AND actor.sub == 'u1'", _ctx())
        assert evaluate_expr("assess.matched_rules == 0 OR actor.sub == 'u1'", _ctx())
        assert evaluate_expr("NOT actor.role == 'viewer'", _ctx())

    def test_precedence_and_binds_tighter_than_or(self) -> None:
        # x=True, y=True, z=False: x || (y && z) = True;(x || y) && z = False
        expr = "actor.role == 'sysop' || assess.matched_rules == 2 && assess.confidence < 0.5"
        assert evaluate_expr(expr, _ctx())

    def test_parentheses_override_precedence(self) -> None:
        expr = "(actor.role == 'sysop' || assess.matched_rules == 2) && assess.confidence < 0.5"
        assert not evaluate_expr(expr, _ctx())

    def test_not_negates_comparison_not_operand(self) -> None:
        # 语法:not_expr := NOT not_expr | cmp → NOT 作用于整个比较
        assert evaluate_expr("NOT assess.confidence >= 1", _ctx())
        assert evaluate_expr("! actor.role == 'viewer'", _ctx())

    def test_nested_parentheses(self) -> None:
        expr = "((assess.confidence >= 0.8) && (actor.sub == 'u1'))"
        assert evaluate_expr(expr, _ctx())


class TestCollections:
    def test_in_list(self) -> None:
        assert evaluate_expr("target.风险等级 in ['高', '中']", _ctx())
        assert not evaluate_expr("target.风险等级 in ['低']", _ctx())

    def test_not_in_list(self) -> None:
        assert evaluate_expr("actor.role not in ['viewer', 'editor']", _ctx())
        assert not evaluate_expr("actor.role not in ['sysop']", _ctx())

    def test_contains_substring(self) -> None:
        assert evaluate_expr("target.风险等级 contains '高'", _ctx())
        assert not evaluate_expr("target.风险等级 contains '低'", _ctx())

    def test_contains_list_membership(self) -> None:
        assert evaluate_expr("target.tags contains '一类'", _ctx())
        assert not evaluate_expr("target.tags contains '二类'", _ctx())


class TestPathsAndLiterals:
    def test_chinese_top_level_path(self) -> None:
        assert evaluate_expr("研判结论 == '预警'", _ctx())

    def test_boolean_literals(self) -> None:
        assert evaluate_expr("target.active == true", _ctx())
        assert evaluate_expr("target.active != false", _ctx())

    def test_bare_path_truthiness(self) -> None:
        assert evaluate_expr("target.active", _ctx())
        assert not evaluate_expr("target.nonexistent", _ctx())

    def test_dotted_numeric_suffix_segment(self) -> None:
        ctx = {"target": {"station2": 5}}
        assert evaluate_expr("target.station2 == 5", ctx)


class TestSafety:
    def test_length_cap_512(self) -> None:
        long_expr = "assess.matched_rules == 1" + " && true" * 100
        assert len(long_expr) > 512
        with pytest.raises(ParsedPredicateError, match="512"):
            evaluate_expr(long_expr, _ctx())

    def test_unparseable_expressions_raise(self) -> None:
        for bad in ["foo ==", "&& x", "a b", "", "== 1", "'s' 't'"]:
            with pytest.raises(ParsedPredicateError):
                evaluate_expr(bad, _ctx())

    def test_unterminated_string_raises(self) -> None:
        with pytest.raises(ParsedPredicateError):
            evaluate_expr("target.a == 'abc", _ctx())

    def test_path_charset_whitelist(self) -> None:
        # 连字符/引号/空白段拒绝(白名单 = unicode 词字符,允许中文)
        for bad in ["target.foo-bar == 1", "target.'q' == 1", "target. == 1", "tar get.a == 1"]:
            with pytest.raises(ParsedPredicateError):
                evaluate_expr(bad, _ctx())

    def test_missing_path_comparison_is_false_not_crash(self) -> None:
        ctx = _ctx()
        assert evaluate_expr("target.nonexistent == 'x'", ctx) is False
        assert evaluate_expr("target.nonexistent != 'x'", ctx) is False
        assert evaluate_expr("target.nonexistent >= 1", ctx) is False

    def test_missing_nested_segment_is_false(self) -> None:
        assert evaluate_expr("assess.nope.deep == 1", _ctx()) is False

    def test_incomparable_types_are_false_not_crash(self) -> None:
        assert evaluate_expr("target.压力 >= 'high'", _ctx()) is False

    def test_no_function_calls_or_eval(self) -> None:
        for bad in ["__import__('os')", "target.a == eval('1')", "target.now() == 1"]:
            with pytest.raises(ParsedPredicateError):
                evaluate_expr(bad, _ctx())


class TestCompilePredicate:
    def test_compile_returns_reusable_parsed(self) -> None:
        parsed = compile_predicate("assess.confidence >= 0.8")
        assert parsed.evaluate(_ctx()) is True
        assert parsed.evaluate({"assess": {"confidence": 0.1}}) is False

    def test_compile_invalid_raises(self) -> None:
        with pytest.raises(ParsedPredicateError):
            compile_predicate("nope ==")

    def test_compile_is_cached_same_source(self) -> None:
        # W3.2 铺路:condition_expr 编译走 lru 缓存,同表达式复用解析结果
        a = compile_predicate("actor.role == 'sysop'")
        b = compile_predicate("actor.role == 'sysop'")
        assert a is b
