"""Source-level ACL enforcement for user SQL (v1.10.7 WP1b/WP1c).

Closes the post-hoc-filtering bypass class (review C2): row filters are
pushed into the query itself (dataset table refs become filtered
subqueries, so the predicate applies to raw rows *before* any user
aggregation), and hidden-column references are rejected at AST level
(sqlglot) so aliasing can no longer smuggle restricted values out.

Both layers are fail-closed: unparseable/unanalyzable SQL on a restricted
dataset is rejected, never passed through.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

from arrow_lake.api.rbac import DatasetACL

logger = logging.getLogger(__name__)

__all__ = ["AclSqlViolation", "enforce_sql_acl"]

# DuckDB dialect used for parse/serialize on both sides.
_DIALECT = "duckdb"


class AclSqlViolation(ValueError):
    """User SQL violates a dataset row/column ACL (maps to HTTP 422/403)."""


def _normalize_predicate(row_filter: str) -> str:
    """Convert admin-UI pyarrow-style predicates to SQL.

    ``DatasetACL.row_filter`` uses ``==``/``!=`` (see ``_apply_row_filter``);
    SQL needs ``=``/``<>``. Values keep their quoting.
    """
    out = row_filter.strip()
    out = out.replace("!=", "<>").replace("==", "=")
    return out


def _alias_map(tree: exp.Expression) -> dict[str, str]:
    """Map every table alias (and bare name) in scope → real table name.

    Keys AND values are lowercased: DuckDB resolves identifiers
    case-insensitively, so ACL matching must compare in a single canonical
    case (sqlglot's qualify() normalizes to lowercase for the duckdb
    dialect — lowercasing here keeps the map consistent regardless).
    """
    mapping: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        real = table.name.lower()
        mapping.setdefault(real, real)
        alias = table.alias
        if alias:
            mapping.setdefault(alias.lower(), real)
    return mapping


def enforce_sql_acl(
    sql: str,
    *,
    get_acl: Callable[[str], DatasetACL | None],
    dataset: str | None = None,
) -> str:
    """Return ACL-enforced SQL, or raise :class:`AclSqlViolation`.

    Args:
        sql: User-submitted SQL (already keyword-blacklist validated).
        get_acl: Callable mapping a table name → DatasetACL for the
            requester's role (``lambda t: checker.get_acl(t, role)``);
            tables with no ACL are untouched.
        dataset: Primary dataset of the endpoint (for error messages).

    Enforcement:
        1. Parse (duckdb dialect). Restricted query that cannot be parsed
           → rejected (fail-closed).
        2. Qualify columns so every reference is bound to its source table.
        3. Column ACL: any reference to a hidden column — direct, aliased,
           inside expressions, inside CTEs — is rejected.
        4. ``SELECT *`` on a column-restricted table is rejected
           (``COUNT(*)`` allowed — row counts leak no column values).
        5. Row filter: each restricted table ref is rewritten into
           ``(SELECT * FROM t WHERE <predicate>)`` so the predicate applies
           before the user's own aggregation/projection.
    """
    # Cheap pre-check: which tables does this SQL reference? Parse failure is
    # fail-closed — the SQL may reference a restricted dataset we can't see.
    # Identifiers are matched case-insensitively (DuckDB semantics): acls is
    # keyed by lowercased table name and every lookup lowercases its probe.
    # The pre-review bug: exact-case keys missed both case-variant refs
    # (FROM mydata vs MyData) AND exact refs after qualify() normalized
    # identifiers to lowercase — mixed-case datasets escaped everything.
    pre = _parse_or_fail(sql)
    referenced = {t.name for t in pre.find_all(exp.Table)}
    acls: dict[str, DatasetACL] = {}
    visible_lower: dict[str, frozenset[str]] = {}
    for tname in referenced:
        acl = get_acl(tname.lower())
        if acl is not None and (acl.row_filter or acl.visible_columns):
            key = tname.lower()
            acls[key] = acl
            visible_lower[key] = frozenset(c.lower() for c in acl.visible_columns)
    if not acls:
        return sql

    tree = _parse_or_fail(sql)
    # 2) qualify: bind unqualified columns to their source tables (incl. CTE
    # internals) so the column check below cannot be dodged by omitting the
    # table prefix. Unanalyzable → fail-closed.
    try:
        tree = qualify(tree, dialect=_DIALECT)
    except Exception as exc:
        raise AclSqlViolation(
            f"SQL could not be analyzed for ACL enforcement: {exc}"
        ) from exc

    # 3) column references (post-qualification binds table for each column)
    aliases = _alias_map(tree)
    for col in tree.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            continue  # handled below
        real = aliases.get(col.table, col.table).lower()
        acl = acls.get(real)
        if (
            acl is not None
            and acl.visible_columns
            and col.name.lower() not in visible_lower[real]
        ):
            raise AclSqlViolation(
                f"Column '{col.name}' is not in the visible column set for dataset "
                f"'{acl.dataset}' — reference removed by column-level ACL"
            )

    # 4) SELECT * / t.* on column-restricted tables
    for star in tree.find_all(exp.Star):
        if isinstance(star.parent, exp.Count):
            continue  # COUNT(*) leaks no column values
        col_parent = star.parent
        real: str | None = None
        if isinstance(col_parent, exp.Column) and col_parent.table:
            real = aliases.get(col_parent.table, col_parent.table).lower()
        restricted = [t for t, a in acls.items() if a.visible_columns]
        if real is not None:
            if real in restricted:
                raise AclSqlViolation(
                    f"SELECT {real}.* is not allowed on column-restricted dataset "
                    f"'{acls[real].dataset}' — list visible columns explicitly"
                )
        elif restricted:
            raise AclSqlViolation(
                "SELECT * is not allowed while column-restricted datasets are "
                f"referenced ({', '.join(sorted(a.dataset for a in acls.values()))}) "
                "— list columns explicitly"
            )

    # 5) row-filter rewrite: table → filtered subquery (predicate on raw rows)
    for table in tree.find_all(exp.Table):
        acl = acls.get(table.name.lower())
        if acl is None or not acl.row_filter:
            continue
        predicate = _normalize_predicate(acl.row_filter)
        sub_sql = f"SELECT * FROM {table.name} WHERE {predicate}"
        sub = exp.Subquery(this=_parse_or_fail(sub_sql))
        alias_arg = table.args.get("alias")
        if alias_arg is not None:
            sub.set("alias", alias_arg.copy())
        table.replace(sub)

    rewritten = tree.sql(dialect=_DIALECT)
    if dataset:
        logger.info(
            "acl_sql_enforced", dataset=dataset,
            restricted_tables=sorted(acls), rewritten=True,
        )
    return rewritten


def _parse_or_fail(sql: str) -> exp.Expression:
    try:
        return sqlglot.parse_one(sql, dialect=_DIALECT)
    except Exception as exc:
        raise AclSqlViolation(
            f"SQL could not be analyzed for ACL enforcement: {exc}"
        ) from exc
