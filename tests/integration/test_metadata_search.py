"""Tests for metadata search bridge — Story 3.9 (integration).

Tests MetadataSearchBridge:
- SQL query against Lance dataset via DuckDB
- SQL validation (SELECT only)
- Table name validation
- Performance with metadata-only queries
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.ingest.storage import LanceStorageManager
from arrow_lake.query.metadata import MetadataQueryResult, MetadataSearchBridge


@pytest.fixture()
def storage(tmp_path: Path) -> LanceStorageManager:
    return LanceStorageManager(str(tmp_path / "lance_data"))


@pytest.fixture()
def bridge(storage: LanceStorageManager) -> MetadataSearchBridge:
    return MetadataSearchBridge(storage)


@pytest.fixture()
def populated(storage: LanceStorageManager, bridge: MetadataSearchBridge) -> str:
    """Create a populated dataset and return its name."""
    table = pa.table(
        {
            "id": ["1", "2", "3", "4", "5"],
            "name": ["Alice", "Bob", "Carol", "Dave", "Eve"],
            "age": [25, 30, 35, 40, 45],
            "city": ["Beijing", "Shanghai", "Beijing", "Shenzhen", "Shanghai"],
            "salary": [5000.0, 8000.0, 6000.0, 12000.0, 9000.0],
        }
    )
    storage.create_dataset("employees", table)
    return "employees"


class TestMetadataSearchBridge:
    """Test MetadataSearchBridge SQL queries."""

    def test_select_all(self, bridge: MetadataSearchBridge, populated: str) -> None:
        result = bridge.query(populated, "SELECT * FROM data")
        assert result.row_count == 5
        assert isinstance(result.table, pa.Table)

    def test_select_with_filter(self, bridge: MetadataSearchBridge, populated: str) -> None:
        result = bridge.query(populated, "SELECT * FROM data WHERE age > 35")
        assert result.row_count == 2

    def test_select_specific_columns(self, bridge: MetadataSearchBridge, populated: str) -> None:
        result = bridge.query(populated, "SELECT name, salary FROM data")
        assert result.row_count == 5
        assert result.table.num_columns == 2

    def test_aggregation(self, bridge: MetadataSearchBridge, populated: str) -> None:
        result = bridge.query(
            populated, "SELECT city, AVG(salary) as avg_salary FROM data GROUP BY city"
        )
        assert result.row_count == 3  # Beijing, Shanghai, Shenzhen

    def test_order_by(self, bridge: MetadataSearchBridge, populated: str) -> None:
        result = bridge.query(populated, "SELECT * FROM data ORDER BY salary DESC")
        assert result.row_count == 5
        salaries = result.table.column("salary").to_pylist()
        assert salaries == sorted(salaries, reverse=True)

    def test_non_select_raises(self, bridge: MetadataSearchBridge, populated: str) -> None:
        from arrow_lake.exceptions import QueryError

        with pytest.raises(QueryError, match="SELECT"):
            bridge.query(populated, "DELETE FROM data WHERE age > 30")

    def test_dangerous_keywords_blocked(self, bridge: MetadataSearchBridge, populated: str) -> None:
        from arrow_lake.exceptions import QueryError

        for sql in [
            "SELECT * FROM data; DROP TABLE data",
            "SELECT * FROM data INSERT INTO data VALUES (1)",
            "SELECT * FROM data ALTER TABLE data",
        ]:
            with pytest.raises(QueryError):
                bridge.query(populated, sql)

    def test_semicolon_blocked(self, bridge: MetadataSearchBridge, populated: str) -> None:
        from arrow_lake.exceptions import QueryError

        with pytest.raises(QueryError, match="Semicolon"):
            bridge.query(populated, "SELECT * FROM data;")

    def test_empty_result(self, bridge: MetadataSearchBridge, populated: str) -> None:
        result = bridge.query(populated, "SELECT * FROM data WHERE age > 100")
        assert result.row_count == 0

    def test_query_result_frozen(self) -> None:
        result = MetadataQueryResult(
            table=pa.table({"a": [1]}),
            row_count=1,
            column_count=1,
            sql="SELECT *",
        )
        with pytest.raises(AttributeError):
            result.row_count = 99  # type: ignore[misc]


class TestMetadataSearchPerformance:
    """Test metadata search performance."""

    def test_large_metadata_query(self, storage: LanceStorageManager, tmp_path: Path) -> None:
        """Query 100K rows of metadata should be fast."""
        import time

        n = 100_000
        table = pa.table(
            {
                "id": [str(i) for i in range(n)],
                "name": [f"user_{i}" for i in range(n)],
                "value": [float(i % 1000) for i in range(n)],
                "category": [f"cat_{i % 50}" for i in range(n)],
            }
        )
        storage.create_dataset("big_table", table)

        bridge = MetadataSearchBridge(storage)

        start = time.perf_counter()
        result = bridge.query(
            "big_table", "SELECT category, COUNT(*) as cnt FROM data GROUP BY category"
        )
        elapsed = time.perf_counter() - start

        assert result.row_count == 50
        assert elapsed < 5.0  # Should be well under 1 second for metadata-only
