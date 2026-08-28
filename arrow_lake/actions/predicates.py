"""谓词表达式 DSL(v1.11.2 MS3 W1.1,F3.1/F3.3 共享语言核心)。

规范:docs_offline/ms3-modeling-language-design.md §4。infix 迷你语法,
**纯手写 tokenizer + 优先级爬升 parser,产 frozen dataclass AST 求值**
——无 eval/exec/函数调用,零新依赖。消费方:ontology_rules.condition_expr
(W3.2,编译失败标记 unruly)与 Action.preconditions / Scenario 条件。

求值语义(fail-safe):上下文缺失 path → None,**任何比较涉及 None 一律
False 不炸**(不对缺失数据做任何断言);类型不可比较(如 str vs int 的
序比较)→ TypeError 捕获为 False。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

__all__ = [
    "MAX_EXPR_LENGTH",
    "ParsedPredicate",
    "ParsedPredicateError",
    "compile_predicate",
    "evaluate",
    "evaluate_expr",
    "is_valid_path",
    "resolve_path",
]

MAX_EXPR_LENGTH = 512

# path 段白名单:unicode 词字符(字母/数字/下划线/中文),与契约列名规则
# 一致地允许中文;连字符不收(与负号歧义——含连字符的列经对齐层换名)。
_PATH_SEGMENT_RE = re.compile(r"^\w+$")

_KEYWORDS = frozenset({"and", "or", "not", "in", "contains", "true", "false"})

_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<number>-?\d+(?:\.\d+)?)
    | (?P<string>'[^']*')
    | (?P<word>[\w.]+)
    | (?P<op>==|!=|>=|<=|>|<)
    | (?P<symand>&&)
    | (?P<symor>\|\|)
    | (?P<symnot>!)
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<comma>,)
    | (?P<lbrack>\[)
    | (?P<rbrack>\])
    """,
    re.VERBOSE,
)


class ParsedPredicateError(ValueError):
    """表达式不可解析/超长/含白名单外构造。"""


def is_valid_path(dotted: str) -> bool:
    """点分路径整体合法(每段均为白名单字符,无空段)。"""
    if not dotted:
        return False
    return all(_PATH_SEGMENT_RE.match(seg) for seg in dotted.split("."))


def resolve_path(context: Mapping[str, Any], segments: tuple[str, ...]) -> Any:
    """沿段下钻取值;任一层缺失 → None(不炸,templates 复用)。"""
    cur: Any = context
    for seg in segments:
        if isinstance(cur, Mapping) and seg in cur:
            cur = cur[seg]
        else:
            return None
    return cur


# --------------------------------------------------------------------------
# AST(frozen dataclass)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Literal:
    value: Any


@dataclass(frozen=True)
class Path:
    segments: tuple[str, ...]


@dataclass(frozen=True)
class ListLit:
    items: tuple[Literal, ...]


@dataclass(frozen=True)
class Compare:
    left: Any
    op: str
    right: Any


@dataclass(frozen=True)
class InExpr:
    left: Any
    items: tuple[Literal, ...]
    negated: bool


@dataclass(frozen=True)
class Contains:
    left: Any
    right: Any


@dataclass(frozen=True)
class And:
    parts: tuple[Any, ...]


@dataclass(frozen=True)
class Or:
    parts: tuple[Any, ...]


@dataclass(frozen=True)
class Not:
    inner: Any


# --------------------------------------------------------------------------
# tokenizer
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Tok:
    kind: str
    text: str
    value: Any = None
    pos: int = 0


def _tokenize(expr: str) -> list[_Tok]:
    toks: list[_Tok] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if m is None:
            raise ParsedPredicateError(f"unexpected character {expr[pos]!r} at position {pos}")
        kind = m.lastgroup or ""
        text = m.group()
        if kind == "ws":
            pos = m.end()
            continue
        if kind == "number":
            value: Any = float(text) if "." in text else int(text)
            toks.append(_Tok("literal", text, value, pos))
        elif kind == "string":
            toks.append(_Tok("literal", text, text[1:-1], pos))
        elif kind == "word":
            lowered = text.lower()
            if lowered in _KEYWORDS:
                toks.append(_Tok(lowered, text, None, pos))
            else:
                if not is_valid_path(text):
                    raise ParsedPredicateError(
                        f"illegal path {text!r} at position {pos} "
                        f"(segments: unicode word characters only)"
                    )
                toks.append(_Tok("path", text, tuple(text.split(".")), pos))
        elif kind == "symand":
            toks.append(_Tok("and", text, None, pos))
        elif kind == "symor":
            toks.append(_Tok("or", text, None, pos))
        elif kind == "symnot":
            toks.append(_Tok("not", text, None, pos))
        else:
            toks.append(_Tok(kind, text, None, pos))
        pos = m.end()
    return toks


# --------------------------------------------------------------------------
# parser(优先级爬升:OR < AND < NOT < 比较/括号)
# --------------------------------------------------------------------------


class _Parser:
    def __init__(self, toks: list[_Tok]) -> None:
        self._toks = toks
        self._i = 0

    def _peek(self, ahead: int = 0) -> _Tok | None:
        j = self._i + ahead
        return self._toks[j] if j < len(self._toks) else None

    def _next(self) -> _Tok:
        tok = self._peek()
        if tok is None:
            raise ParsedPredicateError("unexpected end of expression")
        self._i += 1
        return tok

    def _expect(self, kind: str) -> _Tok:
        tok = self._peek()
        if tok is None or tok.kind != kind:
            got = "end of expression" if tok is None else repr(tok.text)
            raise ParsedPredicateError(f"expected {kind!r}, got {got}")
        return self._next()

    def parse(self) -> Any:
        node = self._or()
        leftover = self._peek()
        if leftover is not None:
            raise ParsedPredicateError(f"trailing tokens after expression: {leftover.text!r}")
        return node

    def _or(self) -> Any:
        parts = [self._and()]
        while (tok := self._peek()) is not None and tok.kind == "or":
            self._next()
            parts.append(self._and())
        return parts[0] if len(parts) == 1 else Or(tuple(parts))

    def _and(self) -> Any:
        parts = [self._not()]
        while (tok := self._peek()) is not None and tok.kind == "and":
            self._next()
            parts.append(self._not())
        return parts[0] if len(parts) == 1 else And(tuple(parts))

    def _not(self) -> Any:
        tok = self._peek()
        if tok is not None and tok.kind == "not":
            self._next()
            return Not(self._not())
        return self._primary()

    def _primary(self) -> Any:
        tok = self._peek()
        if tok is not None and tok.kind == "lparen":
            self._next()
            node = self._or()
            self._expect("rparen")
            return node
        return self._cmp()

    def _cmp(self) -> Any:
        left = self._operand()
        tok = self._peek()
        if tok is None:
            return left
        if tok.kind == "op":
            self._next()
            return Compare(left, tok.text, self._operand())
        if tok.kind == "in":
            self._next()
            return InExpr(left, self._list(), negated=False)
        if tok.kind == "not":
            nxt = self._peek(1)
            if nxt is not None and nxt.kind == "in":
                self._next()
                self._next()
                return InExpr(left, self._list(), negated=True)
            return left
        if tok.kind == "contains":
            self._next()
            return Contains(left, self._operand())
        return left

    def _operand(self) -> Any:
        tok = self._next()
        if tok.kind == "literal":
            return Literal(tok.value)
        if tok.kind == "path":
            return Path(tok.value)
        if tok.kind == "true":
            return Literal(True)
        if tok.kind == "false":
            return Literal(False)
        raise ParsedPredicateError(f"expected operand (literal/path), got {tok.text!r}")

    def _list(self) -> tuple[Literal, ...]:
        self._expect("lbrack")
        items: list[Literal] = []
        while True:
            tok = self._next()
            if tok.kind == "literal":
                items.append(Literal(tok.value))
            elif tok.kind == "true":
                items.append(Literal(True))
            elif tok.kind == "false":
                items.append(Literal(False))
            else:
                raise ParsedPredicateError(f"list items must be literals, got {tok.text!r}")
            sep = self._peek()
            if sep is not None and sep.kind == "comma":
                self._next()
                continue
            break
        self._expect("rbrack")
        if not items:
            raise ParsedPredicateError("list must contain at least one literal")
        return tuple(items)


# --------------------------------------------------------------------------
# evaluator
# --------------------------------------------------------------------------


def _compare(op: str, left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    try:
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
    except TypeError:
        return False
    raise ParsedPredicateError(f"unknown comparison operator {op!r}")


def _truthy(value: Any) -> bool:
    return False if value is None else bool(value)


def evaluate(node: Any, context: Mapping[str, Any]) -> bool:
    """对 AST 求值 → bool。缺失 path → None 语义(比较恒 False)。"""
    if isinstance(node, Literal):
        return _truthy(node.value)
    if isinstance(node, Path):
        return _truthy(resolve_path(context, node.segments))
    if isinstance(node, And):
        return all(evaluate(p, context) for p in node.parts)
    if isinstance(node, Or):
        return any(evaluate(p, context) for p in node.parts)
    if isinstance(node, Not):
        return not evaluate(node.inner, context)
    if isinstance(node, Compare):
        return _compare(
            node.op,
            evaluate_value(node.left, context),
            evaluate_value(node.right, context),
        )
    if isinstance(node, InExpr):
        left = evaluate_value(node.left, context)
        if left is None:
            return False
        found = any(_compare("==", left, it.value) for it in node.items)
        return (not found) if node.negated else found
    if isinstance(node, Contains):
        left = evaluate_value(node.left, context)
        right = evaluate_value(node.right, context)
        if left is None or right is None:
            return False
        try:
            return right in left
        except TypeError:
            return False
    raise ParsedPredicateError(f"unknown AST node {type(node).__name__}")


def evaluate_value(node: Any, context: Mapping[str, Any]) -> Any:
    """求操作数的**值**(比较两侧/contains 两侧用,非布尔)。"""
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, Path):
        return resolve_path(context, node.segments)
    raise ParsedPredicateError(f"expected literal or path operand, got {type(node).__name__}")


# --------------------------------------------------------------------------
# 编译入口(lru 缓存——W3.2 condition_expr 编译复用)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedPredicate:
    source: str
    ast: Any

    def evaluate(self, context: Mapping[str, Any]) -> bool:
        return evaluate(self.ast, context)


@lru_cache(maxsize=512)
def compile_predicate(expr: str) -> ParsedPredicate:
    """解析表达式 → 可复用 ParsedPredicate(同源串复用同一解析结果)。

    不可解析 → ParsedPredicateError(**不被缓存**,每次重抛——错误路径
    便宜且 W3.2 按条标记 unruly 不需要缓存失败)。
    """
    if len(expr) > MAX_EXPR_LENGTH:
        raise ParsedPredicateError(
            f"expression exceeds {MAX_EXPR_LENGTH} characters (got {len(expr)})"
        )
    ast = _Parser(_tokenize(expr)).parse()
    return ParsedPredicate(source=expr, ast=ast)


def evaluate_expr(expr: str, context: Mapping[str, Any]) -> bool:
    """一步到位:编译 + 求值(便捷入口)。"""
    return compile_predicate(expr).evaluate(context)
