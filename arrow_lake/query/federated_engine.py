"""Federated query engine — metadata-driven cross-catalog reads via Gravitino."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import structlog

from arrow_lake.config.gravitino import GravitinoConfig

logger = structlog.get_logger(__name__)

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")
_DANGEROUS_SQL = re.compile(
    r";\s*(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|EXEC|EXECUTE|GRANT|REVOKE)\b",
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

    _FORMAT_READERS = {
        "lance": "read_lance",
        "parquet": "read_parquet",
        "csv": "read_csv",
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
        """Reject SQL containing dangerous statements after semicolons."""
        if _DANGEROUS_SQL.search(sql):
            raise ValueError("SQL contains prohibited statements")

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

    def load_dataset(self, fqn: str) -> Any:
        """Load a dataset by FQN using the appropriate Daft reader based on Gravitino metadata."""
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

        if reader == "read_lance":
            return daft.read_lance(location)
        if reader == "read_parquet":
            return daft.read_parquet(location)
        if reader == "read_csv":
            return daft.read_csv(location)

        raise ValueError(f"Unknown reader: {reader}")

    def cross_catalog_query(
        self,
        catalog_tables: list[tuple[str, str]],
        join_sql: str,
        duckdb_conn: Any = None,
    ) -> pa.Table:
        """Execute a cross-catalog JOIN by loading tables into DuckDB.

        Args:
            catalog_tables: List of (fqn, alias) pairs.
            join_sql: SQL to execute against registered aliases.
            duckdb_conn: Optional existing DuckDB connection.

        Returns:
            PyArrow Table with query results.
        """
        import duckdb

        # Validate inputs
        self._validate_sql(join_sql)
        for fqn, alias in catalog_tables:
            if not self._validate_alias(alias):
                raise ValueError(f"Invalid alias: {alias}")

        conn = duckdb_conn or duckdb.connect(":memory:")
        try:
            for fqn, alias in catalog_tables:
                resolution = self.resolve_table(fqn)
                if resolution is None:
                    raise ValueError(f"Cannot resolve: {fqn}")

                import daft

                location = resolution.location
                if resolution.format in ("lance", "lakehouse-generic"):
                    df = daft.read_lance(location)
                elif resolution.format == "parquet":
                    df = daft.read_parquet(location)
                else:
                    df = daft.read_csv(location)

                arrow_tbl = df.to_arrow()
                if arrow_tbl.num_rows > self._config.federated_query_max_rows:
                    arrow_tbl = arrow_tbl.slice(
                        0, self._config.federated_query_max_rows
                    )
                conn.register(alias, arrow_tbl)

            result = conn.execute(join_sql).fetch_arrow_table()
            return result
        finally:
            if duckdb_conn is None:
                conn.close()
