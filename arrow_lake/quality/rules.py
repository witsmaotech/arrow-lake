"""Declarative quality rule engine for data validation.

Supports configurable rules loaded from JSON/YAML or the API.
Four check types: length, range, regex, duplicate.
Three actions: reject, flag, remove.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import structlog

logger = structlog.get_logger(__name__)

__all__ = ["QualityRuleEngine", "RuleDefinition", "RuleResult"]


@dataclass(frozen=True)
class RuleDefinition:
    """A single declarative quality rule.

    Attributes:
        name: Unique rule name.
        column: Column to check.
        check: Check type — "length", "range", "regex", or "duplicate".
        params: Check-specific parameters.
        action: "reject", "flag", or "remove".
        message: Custom message template (supports {param} substitution).
    """

    name: str
    column: str
    check: str
    params: dict[str, Any] = field(default_factory=dict)
    action: str = "flag"
    message: str = ""

    def __post_init__(self) -> None:
        valid_checks = {"length", "range", "regex", "duplicate"}
        if self.check not in valid_checks:
            raise ValueError(f"check must be one of {valid_checks}, got {self.check!r}")
        valid_actions = {"reject", "flag", "remove"}
        if self.action not in valid_actions:
            raise ValueError(f"action must be one of {valid_actions}, got {self.action!r}")


@dataclass(frozen=True)
class RuleResult:
    """Result of applying a single rule.

    Attributes:
        rule_name: Name of the applied rule.
        action: The action taken.
        affected_count: Number of rows affected.
        message: Human-readable description.
    """

    rule_name: str
    action: str
    affected_count: int
    message: str


class QualityRuleEngine:
    """Declarative quality rule engine.

    Evaluates rules against a PyArrow table and returns results
    describing which rows were rejected, flagged, or removed.

    Usage::

        engine = QualityRuleEngine()
        engine.add_rule(RuleDefinition(
            name="reject_short_text",
            column="text_content",
            check="length",
            params={"min": 10},
            action="reject",
        ))
        results = engine.evaluate(table)
    """

    def __init__(self) -> None:
        self._rules: list[RuleDefinition] = []

    def add_rule(self, rule: RuleDefinition) -> None:
        self._rules.append(rule)

    @property
    def rules(self) -> list[RuleDefinition]:
        return list(self._rules)

    def load_from_dict(self, data: dict[str, Any]) -> int:
        """Load rules from a dict with a "rules" key.

        Returns:
            Number of rules loaded.
        """
        count = 0
        for r in data.get("rules", []):
            self.add_rule(RuleDefinition(
                name=r["name"],
                column=r["column"],
                check=r["check"],
                params=r.get("params", {}),
                action=r.get("action", "flag"),
                message=r.get("message", ""),
            ))
            count += 1
        return count

    def load_from_json(self, path: str) -> int:
        with open(path) as f:
            return self.load_from_dict(json.load(f))

    def clear(self) -> None:
        self._rules.clear()

    def evaluate(self, table: pa.Table) -> list[RuleResult]:
        """Evaluate all rules against the table.

        Returns:
            List of RuleResult for each rule that matched rows.
        """
        results: list[RuleResult] = []
        for rule in self._rules:
            result = self._evaluate_rule(table, rule)
            if result is not None:
                results.append(result)
        return results

    def apply(self, table: pa.Table) -> tuple[pa.Table, list[RuleResult]]:
        """Evaluate rules and apply reject/remove actions.

        Returns:
            Tuple of (filtered_table, results).
        """
        results = self.evaluate(table)
        mask = pa.array([True] * table.num_rows, type=pa.bool_())

        for result in results:
            if result.action in ("reject", "remove"):
                rule = next(r for r in self._rules if r.name == result.rule_name)
                rule_mask = self._get_violation_mask(table, rule)
                mask = pc.and_(mask, pc.invert(rule_mask))

        return table.filter(mask), results

    def _evaluate_rule(self, table: pa.Table, rule: RuleDefinition) -> RuleResult | None:
        if rule.column not in table.column_names:
            logger.warning("rule_column_missing", column=rule.column, rule=rule.name)
            return None

        violation_mask = self._get_violation_mask(table, rule)
        affected = violation_mask.to_pylist().count(True)
        if affected == 0:
            return None

        msg = rule.message
        if not msg:
            msg = f"Rule '{rule.name}': {affected} rows violated {rule.check} check on '{rule.column}'"
        else:
            with contextlib.suppress(KeyError, IndexError):
                msg = msg.format(**rule.params)

        return RuleResult(
            rule_name=rule.name,
            action=rule.action,
            affected_count=affected,
            message=msg,
        )

    def _get_violation_mask(self, table: pa.Table, rule: RuleDefinition) -> pa.ChunkedArray:
        if table.num_rows == 0:
            return pa.array([], type=pa.bool_())
        col = table.column(rule.column)
        check = rule.check
        params = rule.params

        if check == "length":
            return self._check_length(col, params)
        elif check == "range":
            return self._check_range(col, params)
        elif check == "regex":
            return self._check_regex(col, params)
        elif check == "duplicate":
            return self._check_duplicate(col, params)

        return pa.array([False] * table.num_rows, type=pa.bool_())

    @staticmethod
    def _check_length(col: pa.ChunkedArray, params: dict[str, Any]) -> pa.ChunkedArray:
        min_len = params.get("min", 0)
        max_len = params.get("max", float("inf"))
        lengths = pc.utf8_length(col)
        violations = pc.or_(
            pc.less(lengths, min_len),
            pc.greater(lengths, max_len),
        )
        null_mask = col.is_null()
        return pc.and_(violations, pc.invert(null_mask))

    @staticmethod
    def _check_range(col: pa.ChunkedArray, params: dict[str, Any]) -> pa.ChunkedArray:
        min_val = params.get("min")
        max_val = params.get("max")
        violations = pa.array([False] * len(col), type=pa.bool_())
        if min_val is not None:
            violations = pc.or_(
                violations, pc.less(col, min_val)
            )
        if max_val is not None:
            violations = pc.or_(
                violations, pc.greater(col, max_val)
            )
        null_mask = col.is_null()
        return pc.and_(violations, pc.invert(null_mask))

    @staticmethod
    def _check_regex(col: pa.ChunkedArray, params: dict[str, Any]) -> pa.ChunkedArray:
        pattern = params.get("pattern", "")
        invert = params.get("invert", False)
        if not pattern:
            return pa.array([False] * len(col), type=pa.bool_())
        matches = pc.match_substring_regex(col, pattern)
        if invert:
            matches = pc.invert(matches)
        null_mask = col.is_null()
        return pc.and_(matches, pc.invert(null_mask))

    @staticmethod
    def _check_duplicate(col: pa.ChunkedArray, params: dict[str, Any]) -> pa.ChunkedArray:
        values = col.to_pylist()
        seen: dict[Any, bool] = {}
        is_dup = []
        for val in values:
            if val is None:
                is_dup.append(False)
                continue
            key = hashlib.sha256(str(val).encode()).hexdigest() if isinstance(val, (bytes, str)) else val
            if key in seen:
                is_dup.append(True)
            else:
                seen[key] = True
                is_dup.append(False)
        return pa.array(is_dup, type=pa.bool_())
