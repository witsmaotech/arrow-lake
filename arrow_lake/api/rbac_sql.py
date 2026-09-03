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


def _table_key(table: exp.Table) -> str:
    """ACL identity of a table reference, lowercased.

    A two-part reference (``gas_net.segments`` — a container table, DR14
    W3.2) keys on the FULL dotted name: the checker layers it (table-level
    override ``ds::table`` first, container default fallback), and
    ``table.name`` alone ("segments") would miss the dataset ACL entirely
    (fail-open bypass). Plain refs key on the table name.
    """
    if table.db:
        return f"{table.db}.{table.name}".lower()
    return table.name.lower()


def _alias_map(tree: exp.Expression) -> dict[str, str]:
    """Map every table alias (and bare name) in scope → real table name.

    Keys AND values are lowercased: DuckDB resolves identifiers
    case-insensitively, so ACL matching must compare in a single canonical
    case (sqlglot's qualify() normalizes to lowercase for the duckdb
    dialect — lowercasing here keeps the map consistent regardless).

    GLOBAL fallback only — prefer :func:`_resolve_real_table`, which is
    scope-aware: one global map lets an outer alias shadow an inner
    scope's binding (review H-3: ``SELECT (SELECT a.secret FROM restricted
    AS a ...) FROM public_tbl AS a`` hid the restricted table's columns).
    """
    mapping: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        real = _table_key(table)
        mapping.setdefault(real, real)
        alias = table.alias
        if alias:
            mapping.setdefault(alias.lower(), real)
    return mapping


def _local_alias_map(select: exp.Select) -> dict[str, str]:
    """Alias → real table for ONE select's own FROM/JOIN sources only.

    Deliberately does not descend into subqueries in the FROM clause —
    those are separate scopes with their own bindings.
    """
    sources: list[exp.Table] = []
    # sqlglot 30.x keys the FROM clause as "from_" (older: "from")
    frm = select.args.get("from_") or select.args.get("from")
    if frm is not None and isinstance(frm.this, exp.Table):
        sources.append(frm.this)
    for join in select.args.get("joins") or []:
        node = join.this
        if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Table):
            sources.append(node.this)
        elif isinstance(node, exp.Table):
            sources.append(node)
    local: dict[str, str] = {}
    for t in sources:
        real = _table_key(t)
        local[real] = real
        if t.alias:
            local[t.alias.lower()] = real
    return local


def _resolve_real_table(
    column: exp.Column,
    select_maps: dict[int, dict[str, str]],
    global_map: dict[str, str],
) -> str:
    """Resolve a column's table reference the way SQL name resolution does:
    the owning select's own aliases first, then enclosing scopes outward,
    then the global map (CTE names etc.). All names lowercase."""
    name = column.table
    if not name:
        return ""
    probe = name.lower()
    sel = column.parent_select
    while sel is not None:
        local = select_maps.get(id(sel))
        if local and probe in local:
            return local[probe]
        sel = sel.parent_select
    return global_map.get(probe, probe).lower()


def enforce_sql_acl(
    sql: str,
    *,
    get_acl: Callable[[str], DatasetACL | None],
    dataset: str | None = None,
    check_read: Callable[[str], bool] | None = None,
) -> str:
    """Return ACL-enforced SQL, or raise :class:`AclSqlViolation`.

    Args:
        sql: User-submitted SQL (already keyword-blacklist validated).
        get_acl: Callable mapping a table name → DatasetACL for the
            requester's role (``lambda t: checker.get_acl(t, role)``);
            tables with no ACL are untouched.
        dataset: Primary dataset of the endpoint (for error messages).
        check_read: P0-5/P0-6 (review 2026-08-26): callable mapping a
            referenced table key → bool read access. When given, EVERY table
            the SQL references is deny-checked (deny list / denied_actions /
            layered table ACL) — closes both the table-level deny-read gap
            (keys like ``ds.table`` were never consulted) and the pooled-
            session stale-registration leak (a deny on the referenced name
            now rejects before execution instead of relying on the binder).

    Enforcement:
        0. Read deny: every referenced table must be readable when
           ``check_read`` is provided (fail-closed per table).
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
    referenced = {_table_key(t) for t in pre.find_all(exp.Table)}
    if check_read is not None:
        for key in sorted(referenced):
            if not check_read(key):
                raise AclSqlViolation(
                    f"No read access to table '{key}' — reference rejected by "
                    "dataset ACL (deny-read)"
                )
    acls: dict[str, DatasetACL] = {}
    visible_lower: dict[str, frozenset[str]] = {}
    for key in referenced:
        acl = get_acl(key)
        if acl is not None and (acl.row_filter or acl.visible_columns):
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
    select_maps = {
        id(s): _local_alias_map(s) for s in tree.find_all(exp.Select)
    }
    for col in tree.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            continue  # handled below
        real = _resolve_real_table(col, select_maps, aliases)
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

    # 3b) DuckDB COLUMNS() wildcard (review H-1): references columns by
    # string literal/regex — invisible to the exp.Column walk above and
    # unresolvable against the visible set. Fail-closed whenever a
    # column-restricted dataset is in scope (same policy as SELECT *).
    if any(a.visible_columns for a in acls.values()):
        if any(True for _ in tree.find_all(exp.Columns)):
            raise AclSqlViolation(
                "COLUMNS() wildcard is not allowed while column-restricted "
                "datasets are referenced — list columns explicitly"
            )

    # 4) SELECT * / t.* on column-restricted tables
    for star in tree.find_all(exp.Star):
        if isinstance(star.parent, exp.Count):
            continue  # COUNT(*) leaks no column values
        col_parent = star.parent
        real: str | None = None
        if isinstance(col_parent, exp.Column) and col_parent.table:
            real = _resolve_real_table(col_parent, select_maps, aliases)
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
        acl = acls.get(_table_key(table))
        if acl is None or not acl.row_filter:
            continue
        predicate = _normalize_predicate(acl.row_filter)
        # Quoted identifier (review H-5): dataset names may contain '--'
        # (_NAME_PATTERN allows it); an unquoted f-string interpolation made
        # the predicate parse as a comment and silently dropped the filter.
        # The name pattern forbids '"' so double-quoting is unambiguous.
        # Two-part refs (W3.2) keep their schema qualifier so DuckDB still
        # resolves the container table inside the subquery.
        if table.db:
            ref_sql = f'"{table.db}"."{table.name}"'
        else:
            ref_sql = f'"{table.name}"'
        sub_sql = f"SELECT * FROM {ref_sql} WHERE {predicate}"
        sub = exp.Subquery(this=_parse_or_fail(sub_sql))
        alias_arg = table.args.get("alias")
        if alias_arg is not None:
            sub.set("alias", alias_arg.copy())
        table.replace(sub)

    rewritten = tree.sql(dialect=_DIALECT)
    if dataset:
        # Positional %-style: this logger is stdlib (logging.getLogger at module
        # top) — structlog-style kwargs would TypeError in Logger._log once the
        # root level is at INFO (e.g. after a third-party basicConfig flip).
        logger.info(
            "acl_sql_enforced dataset=%s restricted=%s rewritten=True",
            dataset, ",".join(sorted(acls)),
        )
    return rewritten


def _parse_or_fail(sql: str) -> exp.Expression:
    try:
        return sqlglot.parse_one(sql, dialect=_DIALECT)
    except Exception as exc:
        raise AclSqlViolation(
            f"SQL could not be analyzed for ACL enforcement: {exc}"
        ) from exc
