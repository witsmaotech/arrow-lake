"""Contract compiler (DR13 D2, v1.11.0.1 W2.3) — contract → DuckDB predicates.

Every row constraint compiles to a boolean SQL expression where TRUE marks a
VIOLATING row (directly usable as the W3 gate's row mask / dead-letter
filter). NULL semantics: domain checks (enum/range/pattern) pass NULL —
nullability is enforced by the separate ``required`` constraint. References
compile to a NOT EXISTS template; the target relation name is resolved by
the validator at run time (intra-container: the sibling table; cross: the
target dataset's table).
"""

from __future__ import annotations

from dataclasses import dataclass

from arrow_lake.contract.schema import (
    DatasetContract,
    Severity,
    pattern_to_match_regex,
)


def _q(identifier: str) -> str:
    """Quote an identifier for DuckDB (embedded double quotes doubled)."""
    return '"' + identifier.replace('"', '""') + '"'


def _lit(value: str) -> str:
    """Quote a string literal (embedded single quotes doubled)."""
    return "'" + value.replace("'", "''") + "'"


@dataclass(frozen=True)
class RowConstraint:
    table: str
    column: str
    kind: str            # enum | range | pattern | not_null
    severity: Severity
    sql: str             # TRUE = violating row
    message: str


@dataclass(frozen=True)
class ReferenceConstraint:
    table: str
    from_column: str
    to_table: str | None
    to_dataset: str | None
    to_column: str
    severity: Severity = Severity.REJECT

    def render(self, *, target_relation: str, source_alias: str) -> str:
        """Materialize the NOT EXISTS predicate against a concrete relation.

        ``target_relation`` is the (quoted or registered) relation holding the
        referenced ids; ``source_alias`` aliases the constrained batch table.
        IS NOT DISTINCT FROM keeps NULL fk semantics explicit (NULL never
        silently matches): a NULL fk row flags unless the target also holds
        NULL — anchors W3's warn-level decision for out-of-order arrivals.
        """
        return (
            f"NOT EXISTS (SELECT 1 FROM {target_relation} AS _ref "
            f"WHERE _ref.{_q(self.to_column)} IS NOT DISTINCT FROM "
            f"{source_alias}.{_q(self.from_column)})"
        )


@dataclass(frozen=True)
class ConstraintBundle:
    rows: tuple[RowConstraint, ...]
    references: tuple[ReferenceConstraint, ...]

    def row_constraints(self, table: str) -> list[RowConstraint]:
        return [c for c in self.rows if c.table == table]

    def row_constraint(self, table: str, column: str, kind: str) -> RowConstraint | None:
        for c in self.rows:
            if c.table == table and c.column == column and c.kind == kind:
                return c
        return None

    def reference_constraints(self, table: str) -> list[ReferenceConstraint]:
        return [r for r in self.references if r.table == table]


def _column_constraints(table: str, contract: DatasetContract) -> list[RowConstraint]:
    section = contract.tables.get(table)
    if section is None:
        return []
    out: list[RowConstraint] = []
    for rule in section.columns:
        q = _q(rule.name)
        if rule.enum is not None:
            out.append(RowConstraint(
                table=table, column=rule.name, kind="enum",
                severity=Severity.REJECT,
                sql=f"{q} NOT IN ({', '.join(_lit(v) for v in rule.enum)})",
                message=f"column '{rule.name}' outside enum",
            ))
        if rule.range is not None:
            lo, hi = rule.range
            out.append(RowConstraint(
                table=table, column=rule.name, kind="range",
                severity=Severity.REJECT,
                sql=f"NOT ({q} BETWEEN {lo} AND {hi})",
                message=f"column '{rule.name}' outside range [{lo}, {hi}]",
            ))
        if rule.required:
            out.append(RowConstraint(
                table=table, column=rule.name, kind="not_null",
                severity=Severity.REJECT,
                sql=f"{q} IS NULL",
                message=f"column '{rule.name}' is required",
            ))
        # 'unit' is registration-only; 'type' is a schema-level warn (no row SQL)
    if section.identifier is not None:
        ident = section.identifier
        out.append(RowConstraint(
            table=table, column=ident.column, kind="pattern",
            severity=Severity.REJECT,
            sql=(f"NOT regexp_full_match({_q(ident.column)}, "
                 f"{_lit(pattern_to_match_regex(ident.pattern))})"),
            message=f"identifier '{ident.column}' violates pattern '{ident.pattern}'",
        ))
    return out


def compile_contract(contract: DatasetContract) -> ConstraintBundle:
    """Compile all table sections of a contract into a ConstraintBundle."""
    rows: list[RowConstraint] = []
    for table in contract.tables:
        rows.extend(_column_constraints(table, contract))
    refs = tuple(
        ReferenceConstraint(
            table=r.from_table, from_column=r.from_column,
            to_table=r.to_table, to_dataset=r.to_dataset, to_column=r.to_column,
        )
        for r in contract.references
    )
    return ConstraintBundle(rows=tuple(rows), references=refs)
