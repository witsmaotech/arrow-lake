"""Tests for OLAP analytics — Story 5.4 (integration).

Tests OlapSearchBridge with real Lance datasets:
- SELECT * full scan
- GROUP BY + AVG/SUM/COUNT
- GROUP BY + HAVING
- ORDER BY DESC
- LIMIT pagination
- Window ROW_NUMBER
- Window RANK
- COUNT(*)
- Empty results
- Non-SELECT rejection
- Large dataset 100K
- Lake.olap_query() SDK entry point
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.ingest.storage import LanceStorageManager
from arrow_lake.query.olap import OlapQueryResult, OlapSearchBridge

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def storage(tmp_path: Path) -> LanceStorageManager:
    return LanceStorageManager(str(tmp_path / "lance_data"))


@pytest.fixture()
def bridge(storage: LanceStorageManager) -> OlapSearchBridge:
    return OlapSearchBridge(storage)


@pytest.fixture()
def ds_olap(storage: LanceStorageManager) -> str:
    """Create a dataset with structured columns for OLAP queries. Returns dataset name."""
    name = "olap_ds"
    rows = 1000
    table = pa.table(
        {
            "id": [f"item-{i}" for i in range(rows)],
            "category": [f"cat-{i % 5}" for i in range(rows)],
            "region": [f"region-{i % 3}" for i in range(rows)],
            "price": [float(10 + (i * 7) % 100) for i in range(rows)],
            "quantity": [1 + (i * 3) % 20 for i in range(rows)],
        }
    )
    storage.create_dataset(name, table)
    return name


# ---------------------------------------------------------------------------
# Basic SELECT
# ---------------------------------------------------------------------------


class TestBasicSelect:
    """Test basic SELECT queries."""

    def test_select_star(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(ds_olap, "SELECT * FROM olap_ds LIMIT 10")

        assert isinstance(result, OlapQueryResult)
        assert result.row_count == 10
        assert result.column_count == 5
        assert result.sql == "SELECT * FROM olap_ds LIMIT 10"

    def test_select_columns(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(ds_olap, "SELECT category, price FROM olap_ds LIMIT 5")

        assert result.row_count == 5
        assert result.column_count == 2
        assert "category" in result.table.column_names
        assert "price" in result.table.column_names


# ---------------------------------------------------------------------------
# GROUP BY aggregation
# ---------------------------------------------------------------------------


class TestGroupBy:
    """Test GROUP BY aggregation queries."""

    def test_group_by_count(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(
            ds_olap,
            "SELECT category, COUNT(*) as cnt FROM olap_ds GROUP BY category",
        )

        assert result.row_count == 5  # 5 categories
        assert "cnt" in result.table.column_names
        total = sum(result.table.column("cnt").to_pylist())
        assert total == 1000

    def test_group_by_avg(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(
            ds_olap,
            "SELECT category, AVG(price) as avg_price FROM olap_ds GROUP BY category",
        )

        assert result.row_count == 5
        assert "avg_price" in result.table.column_names

    def test_group_by_sum(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(
            ds_olap,
            "SELECT category, SUM(quantity) as total_qty FROM olap_ds GROUP BY category",
        )

        assert result.row_count == 5
        assert "total_qty" in result.table.column_names

    def test_group_by_min_max(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(
            ds_olap,
            "SELECT category, MIN(price) as min_p, MAX(price) as max_p FROM olap_ds GROUP BY category",
        )

        assert result.row_count == 5
        mins = result.table.column("min_p").to_pylist()
        maxs = result.table.column("max_p").to_pylist()
        for mn, mx in zip(mins, maxs, strict=True):
            assert mn <= mx

    def test_group_by_having(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(
            ds_olap,
            "SELECT category, COUNT(*) as cnt FROM olap_ds GROUP BY category HAVING cnt > 150",
        )

        assert result.row_count > 0
        for cnt in result.table.column("cnt").to_pylist():
            assert cnt > 150


# ---------------------------------------------------------------------------
# ORDER BY
# ---------------------------------------------------------------------------


class TestOrderBy:
    """Test ORDER BY sorting."""

    def test_order_by_desc(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(
            ds_olap,
            "SELECT category, COUNT(*) as cnt FROM olap_ds GROUP BY category ORDER BY cnt DESC",
        )

        counts = result.table.column("cnt").to_pylist()
        assert counts == sorted(counts, reverse=True)


# ---------------------------------------------------------------------------
# LIMIT
# ---------------------------------------------------------------------------


class TestLimit:
    """Test LIMIT pagination."""

    def test_limit_5(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(ds_olap, "SELECT * FROM olap_ds LIMIT 5")

        assert result.row_count == 5

    def test_limit_offset(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result1 = bridge.query(ds_olap, "SELECT * FROM olap_ds ORDER BY id LIMIT 5")
        result2 = bridge.query(ds_olap, "SELECT * FROM olap_ds ORDER BY id LIMIT 5 OFFSET 5")

        ids1 = result1.table.column("id").to_pylist()
        ids2 = result2.table.column("id").to_pylist()
        assert len(ids1) == 5
        assert len(ids2) == 5
        assert set(ids1).isdisjoint(set(ids2))


# ---------------------------------------------------------------------------
# Window functions
# ---------------------------------------------------------------------------


class TestWindowFunctions:
    """Test window function queries."""

    def test_row_number(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(
            ds_olap,
            "SELECT *, ROW_NUMBER() OVER (PARTITION BY category ORDER BY price DESC) as rn FROM olap_ds",
        )

        assert result.row_count == 1000
        assert "rn" in result.table.column_names

    def test_rank(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(
            ds_olap,
            "SELECT *, RANK() OVER (PARTITION BY category ORDER BY price DESC) as rnk FROM olap_ds",
        )

        assert result.row_count == 1000
        assert "rnk" in result.table.column_names


# ---------------------------------------------------------------------------
# COUNT(*)
# ---------------------------------------------------------------------------


class TestCountStar:
    """Test COUNT(*) queries."""

    def test_count_star(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(ds_olap, "SELECT COUNT(*) as total FROM olap_ds")

        assert result.row_count == 1
        assert result.table.column("total")[0].as_py() == 1000

    def test_count_with_filter(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(
            ds_olap,
            "SELECT COUNT(*) as total FROM olap_ds WHERE category = 'cat-0'",
        )

        assert result.row_count == 1
        assert result.table.column("total")[0].as_py() == 200


# ---------------------------------------------------------------------------
# Empty results
# ---------------------------------------------------------------------------


class TestEmptyResults:
    """Test empty result handling."""

    def test_no_matching_rows(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        result = bridge.query(
            ds_olap,
            "SELECT * FROM olap_ds WHERE category = 'nonexistent'",
        )

        assert result.row_count == 0
        assert isinstance(result, OlapQueryResult)


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestSecurity:
    """Test SQL injection prevention."""

    def test_non_select_rejected(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        from arrow_lake.exceptions import QueryError

        with pytest.raises(QueryError, match="SELECT"):
            bridge.query(ds_olap, "DROP TABLE olap_ds")

    def test_dangerous_keyword_rejected(self, bridge: OlapSearchBridge, ds_olap: str) -> None:
        from arrow_lake.exceptions import QueryError

        with pytest.raises(QueryError, match="not allowed"):
            bridge.query(ds_olap, "SELECT * FROM olap_ds; INSERT INTO olap_ds VALUES (1)")


# ---------------------------------------------------------------------------
# Large dataset
# ---------------------------------------------------------------------------


class TestLargeDataset:
    """Test OLAP queries on larger datasets."""

    def test_100k_rows(self, storage: LanceStorageManager) -> None:
        import numpy as np

        name = "olap_large"
        rows = 100_000
        rng = np.random.default_rng(42)
        categories = [f"cat-{i % 20}" for i in range(rows)]
        prices = rng.uniform(1.0, 1000.0, rows).tolist()

        table = pa.table(
            {
                "id": [f"item-{i}" for i in range(rows)],
                "category": categories,
                "price": prices,
            }
        )
        storage.create_dataset(name, table)

        bridge = OlapSearchBridge(storage)
        result = bridge.query(
            name,
            "SELECT category, AVG(price) as avg_price, COUNT(*) as cnt FROM olap_large GROUP BY category ORDER BY cnt DESC LIMIT 10",
        )

        assert result.row_count == 10
        assert "avg_price" in result.table.column_names


# ---------------------------------------------------------------------------
# Lake SDK
# ---------------------------------------------------------------------------


class TestLakeSDK:
    """Test Lake.olap_query() SDK entry point."""

    def test_lake_olap_query(
        self,
        storage: LanceStorageManager,
        ds_olap: str,
        tmp_path: Path,
    ) -> None:
        from arrow_lake import Lake
        from arrow_lake.config import ArrowLakeConfig, StorageConfig

        lake = Lake(base_uri=str(tmp_path / "lance_data"), config=ArrowLakeConfig(storage=StorageConfig(backend="local")))
        result = lake.olap_query(
            ds_olap, "SELECT category, COUNT(*) as cnt FROM olap_ds GROUP BY category"
        )

        assert isinstance(result, OlapQueryResult)
        assert result.row_count == 5
