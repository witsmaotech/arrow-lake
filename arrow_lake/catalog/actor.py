"""Catalog Actor — Story 1.8.

Ray Named Actor providing:
- Table metadata CRUD (register, get, list, delete)
- SQL query execution via embedded DuckDB (SELECT only)
- Health diagnostics

Uses DuckDBConnectionPool (Story 1.6) for concurrent SQL access.
All user-facing queries use parameterized execution to prevent SQL injection.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import ray

from arrow_lake.catalog.connection_pool import (
    _SAFE_IDENTIFIER_RE,
    DuckDBConnectionPool,
)
import duckdb

from arrow_lake.exceptions import CatalogError, ErrorCode

_INSERT_SQL = (
    "INSERT INTO catalog_tables (name, schema_json, location, created_at, updated_at, status) "
    "VALUES (?, ?, ?, ?, ?, 'active')"
)
_SELECT_BY_NAME_SQL = (
    "SELECT name, schema_json, location, created_at, updated_at, status "
    "FROM catalog_tables WHERE name = ?"
)
_DELETE_BY_NAME_SQL = "DELETE FROM catalog_tables WHERE name = ?"


def _validate_table_name(name: str) -> None:
    """Validate table name against safe identifier pattern."""
    if not _SAFE_IDENTIFIER_RE.match(name):
        raise CatalogError(
            error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
            message=f"Invalid table name '{name}': must match ^[a-zA-Z_][a-zA-Z0-9_-]*$",
        )


@ray.remote
class CatalogActor:
    """Ray Named Actor for catalog metadata management.

    Manages dataset metadata backed by an embedded DuckDB instance.
    Use as a named actor for single-instance catalog semantics.

    Usage::

        handle = CatalogActor.options(name="catalog").remote()
        ray.get(handle.register_table.remote(...))
    """

    def __init__(self, max_pool_size: int = 5) -> None:
        # Use a temp file so all pool connections share the same schema.
        # :memory: connections are isolated per-connection in DuckDB.
        self._db_dir = tempfile.mkdtemp(prefix="catalog_")
        self._db_path = str(Path(self._db_dir) / "catalog.db")
        self._pool = DuckDBConnectionPool(
            max_size=max_pool_size,
            database=self._db_path,
        )
        self._init_schema()

    def _init_schema(self) -> None:
        """Create catalog tables if they don't exist."""
        create_sql = """
        CREATE TABLE IF NOT EXISTS catalog_tables (
            name VARCHAR PRIMARY KEY,
            schema_json VARCHAR NOT NULL,
            location VARCHAR NOT NULL,
            created_at VARCHAR NOT NULL,
            updated_at VARCHAR NOT NULL,
            status VARCHAR NOT NULL DEFAULT 'active'
        );
        """
        self._pool.execute(create_sql)

        # Migrate existing tables: add status column if missing
        import contextlib

        with contextlib.suppress(Exception):
            self._pool.execute(
                "ALTER TABLE catalog_tables ADD COLUMN status VARCHAR NOT NULL DEFAULT 'active'"
            )

    def ping(self) -> bool:
        """Health check — returns True if actor is alive."""
        return True

    def health(self) -> dict[str, object]:
        """Return actor health diagnostics."""
        pool_health = self._pool.health()
        return {
            "status": "healthy" if not self._pool.is_closed() else "unhealthy",
            "pool_size": pool_health.pool_size,
            "active_connections": pool_health.active_connections,
            "idle_connections": pool_health.idle_connections,
        }

    def register_table(self, name: str, schema_json: str, location: str) -> dict[str, str]:
        """Register a new table in the catalog.

        Args:
            name: Table name (must match ^[a-zA-Z_][a-zA-Z0-9_-]*$).
            schema_json: JSON string describing the table schema.
            location: Storage location URI.

        Returns:
            Dict with table metadata including created_at timestamp.

        Raises:
            CatalogError: If table name is invalid or already exists.
        """
        _validate_table_name(name)
        now = datetime.now(UTC).isoformat()

        try:
            self._pool.execute_params(_INSERT_SQL, (name, schema_json, location, now, now))
        except duckdb.Error as e:
            err_msg = str(e).lower()
            if (
                "unique" in err_msg
                or "primary key" in err_msg
                or "duplicate" in err_msg
                or "constraint" in err_msg
            ):
                raise CatalogError(
                    error_code=ErrorCode.CATALOG_DATASET_ALREADY_EXISTS,
                    message=f"Table '{name}' already exists in catalog",
                ) from e
            raise CatalogError(
                error_code=ErrorCode.CATALOG_CONNECTION_FAILED,
                message=f"Failed to register table '{name}': {e}",
            ) from e

        return {
            "name": name,
            "schema_json": schema_json,
            "location": location,
            "created_at": now,
        }

    def get_table(self, name: str) -> dict[str, str]:
        """Retrieve a table's metadata by name.

        Args:
            name: Table name.

        Returns:
            Dict with table metadata.

        Raises:
            CatalogError: If table not found.
        """
        _validate_table_name(name)

        try:
            rows = self._pool.execute_params(_SELECT_BY_NAME_SQL, (name,))
        except CatalogError:
            raise
        except duckdb.Error as e:
            raise CatalogError(
                error_code=ErrorCode.CATALOG_CONNECTION_FAILED,
                message=f"Failed to get table '{name}': {e}",
            ) from e

        if len(rows) == 0:
            raise CatalogError(
                error_code=ErrorCode.CATALOG_DATASET_NOT_FOUND,
                message=f"Table '{name}' not found in catalog",
            )

        row = rows[0]
        return {
            "name": str(row[0]),
            "schema_json": str(row[1]),
            "location": str(row[2]),
            "created_at": str(row[3]),
            "updated_at": str(row[4]),
            "status": str(row[5]) if len(row) > 5 else "active",
        }

    def list_tables(self) -> list[dict[str, str]]:
        """List all active registered tables.

        Returns:
            List of dicts with table metadata (archived tables excluded).
        """
        try:
            rows = self._pool.execute(
                "SELECT name, schema_json, location, created_at, updated_at, status "
                "FROM catalog_tables WHERE status = 'active' ORDER BY name"
            )
        except duckdb.Error as e:
            raise CatalogError(
                error_code=ErrorCode.CATALOG_CONNECTION_FAILED,
                message=f"Failed to list tables: {e}",
            ) from e

        return [
            {
                "name": str(row[0]),
                "schema_json": str(row[1]),
                "location": str(row[2]),
                "created_at": str(row[3]),
                "updated_at": str(row[4]),
                "status": str(row[5]),
            }
            for row in rows
        ]

    def delete_table(self, name: str, cascade: bool = False, base_uri: str | None = None) -> bool:
        """Delete a table from the catalog.

        Args:
            name: Table name.
            cascade: If True, also remove Lance data files.
            base_uri: Base URI for Lance data (required if cascade=True).

        Returns:
            True if deleted.

        Raises:
            CatalogError: If table not found.
        """
        _validate_table_name(name)

        # Check existence first
        self.get_table(name)

        try:
            self._pool.execute_params(_DELETE_BY_NAME_SQL, (name,))
        except duckdb.Error as e:
            raise CatalogError(
                error_code=ErrorCode.CATALOG_CONNECTION_FAILED,
                message=f"Failed to delete table '{name}': {e}",
            ) from e

        if cascade:
            if base_uri is None:
                raise CatalogError(
                    error_code=ErrorCode.VALIDATION_MISSING_FIELD,
                    message="base_uri is required when cascade=True",
                )
            self._delete_lance_data(base_uri, name)

        return True

    def archive_dataset(self, name: str) -> bool:
        """Archive a dataset (hide from default list).

        Args:
            name: Table name.

        Returns:
            True if archived.

        Raises:
            CatalogError: If dataset not found or already archived.
        """
        _validate_table_name(name)
        meta = self.get_table(name)

        if meta.get("status") == "archived":
            raise CatalogError(
                error_code=ErrorCode.CATALOG_DATASET_ALREADY_EXISTS,
                message=f"Dataset '{name}' is already archived",
            )

        try:
            self._pool.execute_params(
                "UPDATE catalog_tables SET status = 'archived', updated_at = ? WHERE name = ?",
                (datetime.now(UTC).isoformat(), name),
            )
        except duckdb.Error as e:
            raise CatalogError(
                error_code=ErrorCode.CATALOG_CONNECTION_FAILED,
                message=f"Failed to archive dataset '{name}': {e}",
            ) from e

        return True

    def restore_dataset(self, name: str) -> bool:
        """Restore an archived dataset.

        Args:
            name: Table name.

        Returns:
            True if restored.

        Raises:
            CatalogError: If dataset not found.
        """
        _validate_table_name(name)
        self.get_table(name)

        try:
            self._pool.execute_params(
                "UPDATE catalog_tables SET status = 'active', updated_at = ? WHERE name = ?",
                (datetime.now(UTC).isoformat(), name),
            )
        except duckdb.Error as e:
            raise CatalogError(
                error_code=ErrorCode.CATALOG_CONNECTION_FAILED,
                message=f"Failed to restore dataset '{name}': {e}",
            ) from e

        return True

    def _delete_lance_data(self, base_uri: str, name: str) -> None:
        """Remove Lance data files for a dataset."""
        import shutil

        base = Path(base_uri).resolve()
        lance_dir = base / f"{name}.lance"
        # Safety check: lance_dir must still be under base (prevent traversal via name)
        if not str(lance_dir.resolve()).startswith(str(base)):
            raise CatalogError(
                error_code=ErrorCode.VALIDATION_INVALID_CONFIG,
                message=f"Refusing to delete data outside base directory: {lance_dir}",
            )

        if lance_dir.is_dir():
            shutil.rmtree(lance_dir)

    def execute_sql(self, query: str) -> list[tuple[object, ...]]:
        """Execute a read-only SELECT query against the catalog database.

        Args:
            query: SQL query string (must start with SELECT).

        Returns:
            List of result rows.

        Raises:
            CatalogError: If query is not a SELECT statement.
            Exception: If query execution fails.
        """
        stripped = query.strip().upper()
        if not stripped.startswith("SELECT"):
            raise CatalogError(
                error_code=ErrorCode.QUERY_SYNTAX_ERROR,
                message="Only SELECT queries are allowed via execute_sql",
            )

        return self._pool.execute(query)

    def _clear_all(self) -> None:
        """Remove all catalog entries. For testing only."""
        self._pool.execute("DELETE FROM catalog_tables")
