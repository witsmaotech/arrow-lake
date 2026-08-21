"""Centralized input validation for SQL, identifiers, and file paths.

Consolidates validation logic that was previously duplicated across
query bridges, catalog modules, and storage connectors.
"""

from __future__ import annotations

import re

__all__ = [
    "DANGEROUS_SQL_KEYWORDS_RE",
    "SAFE_IDENTIFIER_RE",
    "escape_sql_literal",
    "validate_identifier",
    "validate_sql_safety",
    "validate_where_clause",
]

# ---------------------------------------------------------------------------
# SQL injection prevention
# ---------------------------------------------------------------------------

# Merged superset of all keyword lists from olap.py, vector.py, faceted.py,
# metadata.py, lineage.py, and ingest/storage.py.
DANGEROUS_SQL_KEYWORDS_RE = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|"
    r"GRANT|REVOKE|EXEC|EXECUTE|"
    r"COPY|IMPORT|EXPORT|"
    r"UNION|EXCEPT|INTERSECT|"
    r"ATTACH|DETACH|PRAGMA|LOAD|CALL|SET|COMMENT|RENAME|MERGE"
    r")\b",
    re.IGNORECASE,
)

# P0-1 (C1, 2026-08-21): DuckDB reader/glob table functions must never appear
# in user SQL. `read_text('/proc/self/environ')` returns every container secret
# (JWT key, MinIO root, Redis password, LLM keys); read_csv/read_json reach any
# file under /data/lake; *_scan() functions hit object stores or remote DBs,
# bypassing dataset ACLs. Defense in depth with the session-level
# `SET disabled_filesystems` (query/_db.py) — this layer also covers paths that
# build SQL without a session (where clauses, previews).
# Word-boundary note: `read_csv\b` does NOT match `read_csv_auto` (the trailing
# underscore is a word char), so the _auto variants are listed explicitly.
TABLE_FUNCTION_BLACKLIST_RE = re.compile(
    r"\b("
    r"read_csv_auto|read_csv|"
    r"read_json_auto|read_json|"
    r"read_parquet|read_text|read_blob|read_xlsx|"
    r"parquet_scan|iceberg_scan|delta_scan|"
    r"postgres_scan|mysql_scan|sqlite_scan|"
    r"glob|file_search"
    r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Identifier validation
# ---------------------------------------------------------------------------

SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")

_SQL_ESCAPE_RE = re.compile(r"('|\\)")
_MAX_LITERAL_LENGTH = 10_000


def _is_multi_statement(sql: str) -> bool:
    """Heuristic: >1 top-level SELECT ⇒ multiple pasted statements.

    UNION/INTERSECT/EXCEPT are already blocked by DANGEROUS_SQL_KEYWORDS_RE, so a
    second top-level SELECT is unambiguously a new statement. Strips string
    literals, quoted identifiers, comments, and parenthesized subqueries so legit
    single-statement queries (subqueries, CTEs, cache-bust comments) are not
    falsely flagged. Catches the common "paste several queries as one" case that
    otherwise bypasses the semicolon guard, forces full-dataset materialization,
    and crashes the worker (502).
    """
    s = re.sub(r"'(?:[^']|'')*'", "''", sql)           # string literals
    s = re.sub(r'"(?:""|[^"])*"', '""', s)             # quoted identifiers
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)  # block comments
    s = re.sub(r"--[^\n]*", " ", s)                    # line comments
    prev = None
    while prev != s:                                   # parenthesized subqueries
        prev = s
        s = re.sub(r"\([^()]*\)", "", s)
    return len(re.findall(r"\bSELECT\b", s, re.IGNORECASE)) > 1


def validate_sql_safety(sql: str) -> None:
    """Validate that a SQL string contains no dangerous keywords.

    Args:
        sql: SQL string to validate.

    Raises:
        ValueError: If dangerous SQL keywords are detected.
    """
    if DANGEROUS_SQL_KEYWORDS_RE.search(sql):
        raise ValueError(f"Dangerous SQL keyword detected: {sql}")
    if TABLE_FUNCTION_BLACKLIST_RE.search(sql):
        match = TABLE_FUNCTION_BLACKLIST_RE.search(sql)
        raise ValueError(
            f"Dangerous SQL table function detected: {match.group()!r}. "
            f"Only registered datasets may be queried."
        )
    if ";" in sql:
        raise ValueError(f"Semicolons not allowed in SQL: {sql}")
    if _is_multi_statement(sql):
        raise ValueError(
            f"Multiple SQL statements detected — run one query at a time: {sql[:80]}"
        )


def escape_sql_literal(value: str, max_length: int = _MAX_LITERAL_LENGTH) -> str:
    """Escape a string for safe embedding in a SQL single-quoted literal.

    Handles single quotes and backslashes — the two characters that can
    break out of a DuckDB string literal.  Also truncates excessively long
    values to prevent abuse.

    Args:
        value: The string to escape.
        max_length: Maximum allowed length (default 10 000).

    Returns:
        The escaped string (without surrounding quotes).

    Raises:
        ValueError: If the value exceeds *max_length* or is not a string.
    """
    if not isinstance(value, str):
        raise ValueError(f"SQL literal must be a string, got {type(value).__name__}")
    if len(value) > max_length:
        raise ValueError(f"SQL literal too long ({len(value)} > {max_length})")
    return _SQL_ESCAPE_RE.sub(r"\\\1", value)


def validate_identifier(name: str) -> None:
    """Validate that a name is a safe SQL/table identifier.

    Args:
        name: Identifier string to validate.

    Raises:
        ValueError: If the identifier contains unsafe characters.
    """
    if not SAFE_IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid identifier '{name}': must match {SAFE_IDENTIFIER_RE.pattern}")


def validate_where_clause(where: str) -> None:
    """Validate a WHERE clause for dangerous SQL patterns.

    Centralizes the duplicated _validate_where_clause() logic that
    existed in vector.py, fts.py, and hybrid.py.

    Args:
        where: WHERE clause string to validate.

    Raises:
        ValueError: If dangerous SQL keywords are detected.
    """
    match = DANGEROUS_SQL_KEYWORDS_RE.search(where)
    if match:
        raise ValueError(
            f"Where clause contains dangerous SQL keyword: {match.group()!r}. "
            f"Only SELECT-safe filter expressions are allowed."
        )
    func_match = TABLE_FUNCTION_BLACKLIST_RE.search(where)
    if func_match:
        raise ValueError(
            f"Where clause contains dangerous SQL table function: {func_match.group()!r}."
        )
