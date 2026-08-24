"""Tests for SQL injection prevention strengthening (v1.6.0 Phase 1).

Validates:
- COMMENT, RENAME, MERGE are blocked by validation
- DaftQueryRequest validates SQL before execution
- GRANT/REVOKE remain blocked
"""

from __future__ import annotations

import pytest

from arrow_lake.validation import DANGEROUS_SQL_KEYWORDS_RE, validate_sql_safety


class TestBlockedDDLKeywords:
    """New DDL keywords added in v1.6.0 are blocked."""

    @pytest.mark.parametrize(
        "keyword",
        ["COMMENT", "comment", "RENAME", "rename", "MERGE", "merge"],
    )
    def test_new_ddl_blocked_in_regex(self, keyword: str) -> None:
        sql = f"{keyword} TABLE test"
        assert DANGEROUS_SQL_KEYWORDS_RE.search(sql) is not None

    @pytest.mark.parametrize(
        "keyword",
        ["GRANT", "grant", "REVOKE", "revoke"],
    )
    def test_grant_revoke_still_blocked(self, keyword: str) -> None:
        sql = f"{keyword} ALL ON test TO user"
        assert DANGEROUS_SQL_KEYWORDS_RE.search(sql) is not None

    def test_comment_on_table_blocked(self) -> None:
        with pytest.raises(ValueError, match="Only read-only SELECT|parsed"):
            validate_sql_safety("COMMENT ON TABLE test IS 'hi'")

    def test_rename_table_blocked(self) -> None:
        with pytest.raises(ValueError, match="Only read-only SELECT|parsed"):
            validate_sql_safety("RENAME TABLE old TO new")

    def test_merge_into_blocked(self) -> None:
        with pytest.raises(ValueError, match="Only read-only SELECT|parsed"):
            validate_sql_safety("MERGE INTO target USING src ON target.id = src.id")

    def test_safe_select_passes(self) -> None:
        validate_sql_safety("SELECT name, age FROM users WHERE id = 1")

    def test_safe_select_with_subquery_passes(self) -> None:
        validate_sql_safety("SELECT * FROM (SELECT id FROM items) AS sub")


class TestDaftQueryRequestValidation:
    """DaftQueryRequest model validates SQL via validate_sql_safety."""

    def test_dangerous_sql_in_daft_request_raises(self) -> None:
        from arrow_lake.api.models.query import DaftQueryRequest

        with pytest.raises(Exception, match="forbidden|Dangerous"):
            DaftQueryRequest(
                sql={"query": "DROP TABLE users"},
                dataset_name="test_ds",
            )

    def test_safe_sql_in_daft_request_ok(self) -> None:
        from arrow_lake.api.models.query import DaftQueryRequest

        req = DaftQueryRequest(
            sql={"query": "SELECT * FROM test WHERE id > 0"},
            dataset_name="test_ds",
        )
        assert req.sql.query == "SELECT * FROM test WHERE id > 0"
