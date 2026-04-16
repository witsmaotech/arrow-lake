"""Centralized input validation for SQL, identifiers, and file paths.

Consolidates validation logic that was previously duplicated across
query bridges, catalog modules, and storage connectors.
"""

from __future__ import annotations

import re

__all__ = [
    "DANGEROUS_SQL_KEYWORDS_RE",
    "SAFE_IDENTIFIER_RE",
    "validate_identifier",
    "validate_sql_safety",
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
    r"UNION|EXCEPT|INTERSECT"
    r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Identifier validation
# ---------------------------------------------------------------------------

SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")


def validate_sql_safety(sql: str) -> None:
    """Validate that a SQL string contains no dangerous keywords.

    Args:
        sql: SQL string to validate.

    Raises:
        ValueError: If dangerous SQL keywords are detected.
    """
    if DANGEROUS_SQL_KEYWORDS_RE.search(sql):
        raise ValueError(f"Dangerous SQL keyword detected: {sql}")
    if ";" in sql:
        raise ValueError(f"Semicolons not allowed in SQL: {sql}")


def validate_identifier(name: str) -> None:
    """Validate that a name is a safe SQL/table identifier.

    Args:
        name: Identifier string to validate.

    Raises:
        ValueError: If the identifier contains unsafe characters.
    """
    if not SAFE_IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid identifier '{name}': must match {SAFE_IDENTIFIER_RE.pattern}")
