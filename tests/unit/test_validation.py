"""Tests for validation.py — SQL safety, identifier validation, escape, and WHERE clause."""

from __future__ import annotations

import pytest

from arrow_lake.validation import (
    escape_sql_literal,
    validate_identifier,
    validate_sql_safety,
    validate_where_clause,
)


# ===========================================================================
# validate_sql_safety
# ===========================================================================


class TestValidateSqlSafety:
    def test_safe_select(self) -> None:
        validate_sql_safety("SELECT id, name FROM users WHERE age > 18")

    def test_safe_with_cte(self) -> None:
        validate_sql_safety("WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_dangerous_drop(self) -> None:
        with pytest.raises(ValueError, match="Dangerous SQL keyword"):
            validate_sql_safety("DROP TABLE users")

    def test_dangerous_insert(self) -> None:
        with pytest.raises(ValueError, match="Dangerous SQL keyword"):
            validate_sql_safety("INSERT INTO users VALUES (1)")

    def test_dangerous_delete(self) -> None:
        with pytest.raises(ValueError, match="Dangerous SQL keyword"):
            validate_sql_safety("DELETE FROM users")

    def test_dangerous_alter(self) -> None:
        with pytest.raises(ValueError, match="Dangerous SQL keyword"):
            validate_sql_safety("ALTER TABLE users ADD COLUMN x INT")

    def test_semicolons_rejected(self) -> None:
        with pytest.raises(ValueError, match="Dangerous|Semicolons"):
            validate_sql_safety("SELECT 1; DROP TABLE users")

    def test_union_rejected(self) -> None:
        with pytest.raises(ValueError, match="Dangerous SQL keyword"):
            validate_sql_safety("SELECT * FROM a UNION SELECT * FROM b")

    def test_attach_rejected(self) -> None:
        with pytest.raises(ValueError, match="Dangerous SQL keyword"):
            validate_sql_safety("ATTACH DATABASE 'evil.db'")

    def test_case_insensitive(self) -> None:
        with pytest.raises(ValueError, match="Dangerous SQL keyword"):
            validate_sql_safety("drop table users")


# ===========================================================================
# escape_sql_literal
# ===========================================================================


class TestEscapeSqlLiteral:
    def test_plain_string(self) -> None:
        assert escape_sql_literal("hello") == "hello"

    def test_escapes_single_quote(self) -> None:
        assert escape_sql_literal("it's") == "it\\'s"

    def test_escapes_backslash(self) -> None:
        assert escape_sql_literal("a\\b") == "a\\\\b"

    def test_rejects_non_string(self) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            escape_sql_literal(123)  # type: ignore[arg-type]

    def test_rejects_too_long(self) -> None:
        with pytest.raises(ValueError, match="too long"):
            escape_sql_literal("x" * 10001, max_length=10000)

    def test_custom_max_length(self) -> None:
        assert escape_sql_literal("short", max_length=10) == "short"

    def test_at_max_length_ok(self) -> None:
        assert escape_sql_literal("x" * 100, max_length=100) == "x" * 100


# ===========================================================================
# validate_identifier
# ===========================================================================


class TestValidateIdentifier:
    def test_valid_simple(self) -> None:
        validate_identifier("users")

    def test_valid_with_underscore(self) -> None:
        validate_identifier("my_table")

    def test_valid_with_digits(self) -> None:
        validate_identifier("table_123")

    def test_valid_with_dash(self) -> None:
        validate_identifier("my-table")

    def test_rejects_starts_with_digit(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            validate_identifier("123table")

    def test_rejects_special_chars(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            validate_identifier("table;drop")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            validate_identifier("")

    def test_rejects_space(self) -> None:
        with pytest.raises(ValueError, match="Invalid identifier"):
            validate_identifier("my table")


# ===========================================================================
# validate_where_clause
# ===========================================================================


class TestValidateWhereClause:
    def test_safe_clause(self) -> None:
        validate_where_clause("status = 'active' AND age > 18")

    def test_safe_with_function(self) -> None:
        validate_where_clause("LOWER(name) = 'alice'")

    def test_rejects_drop(self) -> None:
        with pytest.raises(ValueError, match="dangerous SQL keyword"):
            validate_where_clause("1=1; DROP TABLE users")

    def test_rejects_delete(self) -> None:
        with pytest.raises(ValueError, match="dangerous SQL keyword"):
            validate_where_clause("DELETE FROM users WHERE 1=1")

    def test_rejects_insert(self) -> None:
        with pytest.raises(ValueError, match="dangerous SQL keyword"):
            validate_where_clause("INSERT INTO t VALUES(1)")
