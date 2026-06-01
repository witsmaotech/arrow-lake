"""Federated query engine — metadata-driven cross-catalog reads via Gravitino."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, ClassVar

import pyarrow as pa
import structlog

from arrow_lake.config.gravitino import GravitinoConfig

logger = structlog.get_logger(__name__)

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")
_DANGEROUS_SQL = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|GRANT|REVOKE|UNION|EXCEPT|INTERSECT)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TableResolution:
    """Resolved table metadata from Gravitino."""

    catalog: str
    schema: str
    table: str
    format: str  # lance, parquet, csv
    location: str
    columns: list[dict[str, str]]


class FederatedQueryEngine:
    """Metadata-driven federated query: resolves table locations and formats from
    Gravitino, then loads with the appropriate Daft reader.

    Supports cross-catalog JOIN by loading into DuckDB for SQL execution.
    """

    _FORMAT_READERS: ClassVar[dict[str, str]] = {
        "lance": "read_lance",
        "parquet": "read_parquet",
        "csv": "read_csv",
        "iceberg": "read_iceberg",
    }

    def __init__(self, config: GravitinoConfig) -> None:
        self._config = config

    @staticmethod
    def _validate_fqn(fqn: str) -> tuple[str, str, str] | None:
        """Parse and validate a fully qualified name. Returns (catalog, schema, table) or None."""
        parts = fqn.split(".")
        if len(parts) == 3:
            catalog, schema_name, table = parts
        elif len(parts) == 1:
            catalog, schema_name, table = "lance-catalog", "arrow_lake", parts[0]
        else:
            return None
        if not all(_SAFE_ID.match(p) for p in (catalog, schema_name, table)):
            logger.warning("federated_engine.invalid_identifier", fqn=fqn)
            return None
        return catalog, schema_name, table

    @staticmethod
    def _validate_alias(alias: str) -> bool:
        """Check alias is a safe SQL identifier."""
        return bool(_SAFE_ID.match(alias))

    @staticmethod
    def _validate_sql(sql: str) -> None:
        """Reject SQL containing dangerous statements or non-SELECT/WITH starters."""
        stripped = sql.strip().upper()
        if not stripped.startswith("SELECT") and not stripped.startswith("WITH"):
            raise ValueError("Only SELECT/WITH queries are allowed")
        if _DANGEROUS_SQL.search(sql):
            raise ValueError("SQL contains prohibited statements")
        if ";" in sql.rstrip(";"):
            raise ValueError("Multi-statement SQL is not allowed")

    def resolve_table(self, fqn: str) -> TableResolution | None:
        """Resolve a fully qualified name (catalog.schema.table) to table metadata."""
        parsed = self._validate_fqn(fqn)
        if parsed is None:
            return None
        catalog, schema_name, table = parsed

        try:
            from urllib.request import Request, urlopen

            url = (
                f"{self._config.uri}/api/metalakes/{self._config.metalake}"
                f"/catalogs/{catalog}/schemas/{schema_name}/tables/{table}"
            )
            req = Request(url)
            req.add_header("Accept", "application/vnd.gravitino.v1+json")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            tbl = data.get("table", {})
            props = tbl.get("properties", {})
            location = props.get("location", "")
            fmt = props.get("format", "lance")

            # Infer format from location if not set
            if not fmt or fmt == "lakehouse-generic":
                if location.endswith(".parquet") or "parquet" in location:
                    fmt = "parquet"
                elif location.endswith(".csv"):
                    fmt = "csv"
                else:
                    fmt = "lance"

            columns = [
                {"name": c.get("name", ""), "type": c.get("type", "string")}
                for c in tbl.get("columns", [])
            ]

            return TableResolution(
                catalog=catalog,
                schema=schema_name,
                table=table,
                format=fmt,
                location=location,
                columns=columns,
            )
        except Exception:
            logger.debug("federated_engine.resolve_failed", fqn=fqn, exc_info=True)
            return None

    def load_dataset(self, fqn: str, *, where: str | None = None) -> Any:
        """Load a dataset by FQN using the appropriate Daft reader based on Gravitino metadata.

        Args:
            fqn: Fully qualified name (catalog.schema.table or simple table name).
            where: Optional Daft expression string for predicate pushdown
                    (e.g. ``"x > 10"``, ``"status = 'active'"``).
                    Applied via Daft ``.where()`` before materialization.

        Returns:
            Daft DataFrame, optionally filtered.
        """
        import daft

        resolution = self.resolve_table(fqn)
        if resolution is None:
            raise ValueError(f"Cannot resolve table: {fqn}")

        location = resolution.location
        if not location:
            raise ValueError(f"No location for table: {fqn}")

        reader = self._FORMAT_READERS.get(resolution.format)
        if reader is None:
            raise ValueError(f"Unsupported format: {resolution.format}")

        if not location.startswith(("s3://", "gs://", "file://", "/", "./")):
            raise ValueError(f"Invalid location: {location}")

        if reader == "read_lance":
            df = daft.read_lance(location)
        elif reader == "read_parquet":
            df = daft.read_parquet(location)
        elif reader == "read_iceberg":
            df = daft.read_iceberg(location)
        else:
            df = daft.read_csv(location)

        if where:
            df = df.where(where)

        return df

    @staticmethod
    def _extract_simple_filters(sql: str) -> dict[str, str]:
        """Extract simple equality/range filters from a WHERE clause.

        Parses patterns like ``col = value`` and ``col > value`` (AND-combined).
        Returns a dict of ``{col: expr_string}`` for Daft ``.where()``.
        Complex expressions (OR, nested parens, functions) are skipped.

        This is best-effort — callers should pass unhandled filters to DuckDB.
        """
        # Normalize whitespace
        sql_upper = " ".join(sql.upper().split())

        # Find WHERE clause
        where_idx = sql_upper.rfind(" WHERE ")
        if where_idx == -1:
            return {}

        where_clause = sql[where_idx + 7:].strip()
        # Strip trailing ORDER BY / GROUP BY / LIMIT / HAVING
        for tail in (" ORDER BY ", " GROUP BY ", " LIMIT ", " HAVING "):
            idx = where_clause.upper().find(tail)
            if idx != -1:
                where_clause = where_clause[:idx]

        # Only handle simple AND-combined predicates (no OR, no nested parens)
        if " OR " in where_clause.upper() or "(" in where_clause:
            return {}

        filters: dict[str, str] = {}
        # Match patterns: col op value (op: =, !=, <>, <, >, <=, >=)
        _FILTER_RE = re.compile(
            r"(\w+)\s*(=|!=|<>|>=|<=|>|<)\s*"
            r"('(?:[^'\\]|\\.)*'|\d+(?:\.\d+)?|TRUE|FALSE|NULL)",
            re.IGNORECASE,
        )
        for m in _FILTER_RE.finditer(where_clause):
            col, op, val = m.group(1), m.group(2), m.group(3)
            # Skip aliases and SQL keywords
            if col.upper() in ("AND", "OR", "NOT", "WHERE", "IN", "BETWEEN", "LIKE",
                               "IS", "NULL", "TRUE", "FALSE", "SELECT", "FROM"):
                continue
            filters[col] = f"{col} {op} {val}"

        return filters

    def cross_catalog_query(
        self,
        catalog_tables: list[tuple[str, str]],
        join_sql: str,
        duckdb_conn: Any = None,
        *,
        pushdown_filters: bool = True,
    ) -> pa.Table:
        """Execute a cross-catalog JOIN by loading tables into DuckDB.

        Args:
            catalog_tables: List of (fqn, alias) pairs.
            join_sql: SQL to execute against registered aliases.
            duckdb_conn: Optional existing DuckDB connection.
            pushdown_filters: When True, extract simple equality/range filters
                from the WHERE clause and push down via Daft ``.where()`` to
                reduce I/O before DuckDB materialization.

        Returns:
            PyArrow Table with query results.
        """
        import duckdb

        # Validate inputs
        self._validate_sql(join_sql)
        for _fqn, alias in catalog_tables:
            if not self._validate_alias(alias):
                raise ValueError(f"Invalid alias: {alias}")

        # Best-effort extract simple filters for pushdown
        simple_filters = self._extract_simple_filters(join_sql) if pushdown_filters else {}

        conn = duckdb_conn or duckdb.connect(":memory:")
        try:
            for fqn, alias in catalog_tables:
                resolution = self.resolve_table(fqn)
                if resolution is None:
                    raise ValueError(f"Cannot resolve: {fqn}")

                # Build pushdown filter for this table's columns
                table_filter = None
                if simple_filters and resolution.columns:
                    col_names = {c["name"] for c in resolution.columns}
                    matched = {k: v for k, v in simple_filters.items() if k in col_names}
                    if matched:
                        table_filter = " AND ".join(matched.values())
                        logger.debug(
                            "federated_engine.predicate_pushdown",
                            table=fqn,
                            filter=table_filter,
                        )

                df = self.load_dataset(fqn, where=table_filter)

                arrow_tbl = df.to_arrow()
                if arrow_tbl.num_rows > self._config.federated_query_max_rows:
                    arrow_tbl = arrow_tbl.slice(
                        0, self._config.federated_query_max_rows
                    )
                    logger.info(
                        "federated_engine.rows_limited",
                        table=fqn,
                        rows=arrow_tbl.num_rows,
                    )
                conn.register(alias, arrow_tbl)

            result = conn.execute(join_sql).fetch_arrow_table()
            return result
        finally:
            if duckdb_conn is None:
                conn.close()
