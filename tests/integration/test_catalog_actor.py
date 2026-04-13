"""Tests for arrow_lake.catalog.actor — Story 1.8.

Tests CatalogActor (Ray Named Actor with embedded DuckDB):
- Table registration / retrieval / listing / deletion
- SQL query execution
- Named Actor registration and retrieval
- Duplicate table handling

Note: Ray raises UnserializableException for custom exceptions
that cannot be deserialized. We catch that and verify the message.
"""

from __future__ import annotations

import contextlib

import pytest

# Ray integration tests require the ray runtime available
pytest.importorskip("ray")

import ray
from arrow_lake.catalog.actor import CatalogActor


def _assert_catalog_error(exc: BaseException, match: str) -> None:
    """Assert that a Ray exception wraps a CatalogError with matching message."""
    error_str = str(exc)
    assert match in error_str, f"Expected '{match}' in error: {error_str}"


@pytest.fixture(scope="module")
def ray_instance():
    """Start a local Ray instance for the test module."""
    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True)
    yield
    if ray.is_initialized():
        ray.shutdown()


@pytest.fixture
def catalog(ray_instance) -> ray.actor.ActorHandle:
    """Get or create a CatalogActor for testing."""
    try:
        actor = ray.get_actor("test_catalog")
        ray.kill(actor)
    except ValueError:
        pass

    handle = CatalogActor.options(name="test_catalog").remote()
    ray.get(handle.ping.remote())  # Wait for actor to be ready
    ray.get(handle._clear_all.remote())  # Clean slate for each test
    actor = ray.get_actor("test_catalog")
    yield actor
    with contextlib.suppress(Exception):
        ray.kill(actor)


class TestCatalogActorRegistration:
    """Test CatalogActor as a Ray Named Actor."""

    def test_actor_is_registered(self, ray_instance) -> None:
        """CatalogActor can be registered as a named actor."""
        try:
            actor = ray.get_actor("reg_test_catalog")
            ray.kill(actor)
        except ValueError:
            pass

        handle = CatalogActor.options(name="reg_test_catalog").remote()
        ray.get(handle.ping.remote())  # Wait for ready
        assert ray.get_actor("reg_test_catalog") is not None

        ray.kill(ray.get_actor("reg_test_catalog"))

    def test_actor_retrieval(self, ray_instance) -> None:
        """Same named actor can be retrieved by name."""
        try:
            actor = ray.get_actor("retrieval_test_catalog")
            ray.kill(actor)
        except ValueError:
            pass

        handle1 = CatalogActor.options(name="retrieval_test_catalog").remote()
        ray.get(handle1.ping.remote())  # Wait for ready
        handle2 = ray.get_actor("retrieval_test_catalog")

        # Both handles reference the same actor
        result1 = ray.get(handle1.ping.remote())
        result2 = ray.get(handle2.ping.remote())
        assert result1 is True
        assert result2 is True

        ray.kill(ray.get_actor("retrieval_test_catalog"))


class TestCatalogTableCRUD:
    """Test table metadata CRUD operations."""

    def test_register_table(self, catalog) -> None:
        """Register a new table in the catalog."""
        result = ray.get(
            catalog.register_table.remote(
                name="users",
                schema_json='{"columns": ["id", "name"], "types": ["int64", "string"]}',
                location="s3://arrow-lake/users.lance",
            )
        )
        assert result["name"] == "users"
        assert "created_at" in result

    def test_register_duplicate_table_raises(self, catalog) -> None:
        """Registering a duplicate table name raises CatalogError."""
        ray.get(
            catalog.register_table.remote(
                name="dup_table",
                schema_json='{"columns": ["id"]}',
                location="s3://arrow-lake/dup.lance",
            )
        )

        with pytest.raises(ray.exceptions.UnserializableException) as exc_info:
            ray.get(
                catalog.register_table.remote(
                    name="dup_table",
                    schema_json='{"columns": ["id"]}',
                    location="s3://arrow-lake/dup2.lance",
                )
            )
        _assert_catalog_error(exc_info.value, "already exists")

    def test_get_table(self, catalog) -> None:
        """Retrieve a registered table by name."""
        ray.get(
            catalog.register_table.remote(
                name="get_test",
                schema_json='{"columns": ["x"]}',
                location="s3://arrow-lake/get_test.lance",
            )
        )

        result = ray.get(catalog.get_table.remote("get_test"))
        assert result["name"] == "get_test"
        assert result["location"] == "s3://arrow-lake/get_test.lance"

    def test_get_nonexistent_table_raises(self, catalog) -> None:
        """Getting a nonexistent table raises CatalogError."""
        with pytest.raises(ray.exceptions.UnserializableException) as exc_info:
            ray.get(catalog.get_table.remote("nonexistent_xyz"))
        _assert_catalog_error(exc_info.value, "not found")

    def test_list_tables(self, catalog) -> None:
        """List all registered tables."""
        ray.get(
            catalog.register_table.remote(
                name="table_a",
                schema_json='{"columns": ["a"]}',
                location="s3://arrow-lake/a.lance",
            )
        )
        ray.get(
            catalog.register_table.remote(
                name="table_b",
                schema_json='{"columns": ["b"]}',
                location="s3://arrow-lake/b.lance",
            )
        )

        tables = ray.get(catalog.list_tables.remote())
        names = [t["name"] for t in tables]
        assert "table_a" in names
        assert "table_b" in names

    def test_delete_table(self, catalog) -> None:
        """Delete a registered table."""
        ray.get(
            catalog.register_table.remote(
                name="del_test",
                schema_json='{"columns": ["d"]}',
                location="s3://arrow-lake/del.lance",
            )
        )

        result = ray.get(catalog.delete_table.remote("del_test"))
        assert result is True

        with pytest.raises(ray.exceptions.UnserializableException) as exc_info:
            ray.get(catalog.get_table.remote("del_test"))
        _assert_catalog_error(exc_info.value, "not found")

    def test_delete_nonexistent_table_raises(self, catalog) -> None:
        """Deleting a nonexistent table raises CatalogError."""
        with pytest.raises(ray.exceptions.UnserializableException) as exc_info:
            ray.get(catalog.delete_table.remote("ghost_table"))
        _assert_catalog_error(exc_info.value, "not found")


class TestCatalogSQLQuery:
    """Test SQL query execution via embedded DuckDB."""

    def test_execute_sql_returns_results(self, catalog) -> None:
        """Execute a simple SQL query and get results."""
        ray.get(
            catalog.register_table.remote(
                name="sql_test",
                schema_json='{"columns": ["id", "value"]}',
                location="s3://arrow-lake/sql_test.lance",
            )
        )

        results = ray.get(catalog.execute_sql.remote("SELECT count(*) FROM catalog_tables"))
        assert len(results) >= 1

    def test_execute_sql_invalid_raises(self, catalog) -> None:
        """Invalid SQL raises a query error."""
        with pytest.raises(ray.exceptions.UnserializableException):
            ray.get(catalog.execute_sql.remote("INVALID SQL QUERY"))

    def test_execute_sql_blocks_delete(self, catalog) -> None:
        """DELETE statements are blocked by execute_sql."""
        with pytest.raises(ray.exceptions.UnserializableException) as exc_info:
            ray.get(catalog.execute_sql.remote("DELETE FROM catalog_tables"))
        _assert_catalog_error(exc_info.value, "Only SELECT")

    def test_execute_sql_blocks_drop(self, catalog) -> None:
        """DROP statements are blocked by execute_sql."""
        with pytest.raises(ray.exceptions.UnserializableException) as exc_info:
            ray.get(catalog.execute_sql.remote("DROP TABLE catalog_tables"))
        _assert_catalog_error(exc_info.value, "Only SELECT")


class TestCatalogSQLInjection:
    """Test SQL injection protection."""

    def test_sql_injection_in_name_rejected(self, catalog) -> None:
        """Table names with SQL injection patterns are rejected."""
        with pytest.raises(ray.exceptions.UnserializableException) as exc_info:
            ray.get(
                catalog.register_table.remote(
                    name="'; DROP TABLE catalog_tables; --",
                    schema_json='{"columns": ["id"]}',
                    location="s3://evil.lance",
                )
            )
        _assert_catalog_error(exc_info.value, "Invalid table name")

    def test_sql_injection_in_get_rejected(self, catalog) -> None:
        """get_table with SQL injection pattern in name is rejected."""
        with pytest.raises(ray.exceptions.UnserializableException) as exc_info:
            ray.get(catalog.get_table.remote("' OR 1=1 --"))
        _assert_catalog_error(exc_info.value, "Invalid table name")

    def test_schema_json_with_quotes_safe(self, catalog) -> None:
        """schema_json containing single quotes is handled safely via parameterized queries."""
        schema = '{"columns": ["it\'s"], "desc": "O\'Brien\'s data"}'
        result = ray.get(
            catalog.register_table.remote(
                name="safe_table",
                schema_json=schema,
                location="s3://safe.lance",
            )
        )
        assert result["name"] == "safe_table"

        retrieved = ray.get(catalog.get_table.remote("safe_table"))
        assert retrieved["schema_json"] == schema


class TestCatalogActorHealth:
    """Test actor health and diagnostics."""

    def test_ping(self, catalog) -> None:
        """Actor responds to ping."""
        assert ray.get(catalog.ping.remote()) is True

    def test_health_check(self, catalog) -> None:
        """Actor returns health info."""
        health = ray.get(catalog.health.remote())
        assert health["status"] == "healthy"
        assert "pool_size" in health
