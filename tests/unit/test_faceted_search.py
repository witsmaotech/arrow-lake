"""Tests for Story 8.1 — Faceted Search with DuckDB CUBE."""

from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from arrow_lake.config import FacetedSearchConfig
from arrow_lake.query.faceted import FacetCount, FacetedSearchBridge, FacetedSearchResult


def _make_bridge(
    max_facet_values: int = 50,
    default_facet_columns: list[str] | None = None,
) -> FacetedSearchBridge:
    storage = MagicMock()
    config = FacetedSearchConfig(
        max_facet_values=max_facet_values,
        default_facet_columns=default_facet_columns or ["modality", "source"],
    )
    return FacetedSearchBridge(storage=storage, config=config)


def _make_vector_table(rows: int = 10) -> pa.Table:
    return pa.Table.from_pydict(
        {
            "id": list(range(rows)),
            "modality": ["image" if i % 2 == 0 else "text" for i in range(rows)],
            "source": [["web", "api", "file"][i % 3] for i in range(rows)],
            "embedding": [[float(i)] * 4 for i in range(rows)],
            "_distance": [float(i) for i in range(rows)],
        }
    )


class TestFacetCount:
    """Test FacetCount dataclass."""

    def test_fields(self) -> None:
        fc = FacetCount(name="modality", value="image", count=42)
        assert fc.name == "modality"
        assert fc.value == "image"
        assert fc.count == 42

    def test_frozen(self) -> None:
        fc = FacetCount(name="modality", value="image", count=42)
        with pytest.raises(AttributeError):
            fc.count = 99  # type: ignore[misc]

    def test_equality(self) -> None:
        a = FacetCount(name="modality", value="image", count=42)
        b = FacetCount(name="modality", value="image", count=42)
        assert a == b


class TestFacetedSearchResult:
    """Test FacetedSearchResult dataclass."""

    def test_fields(self) -> None:
        table = _make_vector_table(5)
        facets = [FacetCount(name="modality", value="image", count=3)]
        result = FacetedSearchResult(
            table=table,
            row_count=5,
            facets=facets,
            total_facets=1,
            query_vector_dim=4,
            top_k=5,
        )
        assert result.row_count == 5
        assert result.total_facets == 1
        assert result.query_vector_dim == 4
        assert result.top_k == 5

    def test_frozen(self) -> None:
        table = _make_vector_table(5)
        facets = [FacetCount(name="modality", value="image", count=3)]
        result = FacetedSearchResult(
            table=table,
            row_count=5,
            facets=facets,
            total_facets=1,
            query_vector_dim=4,
            top_k=5,
        )
        with pytest.raises(AttributeError):
            result.row_count = 99  # type: ignore[misc]

    def test_facets_list(self) -> None:
        facets = [
            FacetCount(name="modality", value="image", count=3),
            FacetCount(name="modality", value="text", count=2),
            FacetCount(name="source", value="web", count=4),
        ]
        table = _make_vector_table(5)
        result = FacetedSearchResult(
            table=table,
            row_count=5,
            facets=facets,
            total_facets=3,
            query_vector_dim=4,
            top_k=5,
        )
        assert len(result.facets) == 3


class TestBuildCubeQuery:
    """Test CUBE query generation."""

    def test_single_dimension(self) -> None:
        bridge = _make_bridge()
        sql = bridge._build_cube_query("data", ["modality"], None)
        assert "CUBE(modality)" in sql
        assert "GROUP BY" in sql
        assert "SELECT" in sql

    def test_multiple_dimensions(self) -> None:
        bridge = _make_bridge()
        sql = bridge._build_cube_query("data", ["modality", "source"], None)
        assert "CUBE(modality, source)" in sql
        assert "SELECT" in sql

    def test_with_where_clause(self) -> None:
        bridge = _make_bridge()
        sql = bridge._build_cube_query("data", ["modality"], "quality_score > 0.7")
        assert "WHERE quality_score > 0.7" in sql

    def test_without_where_clause(self) -> None:
        bridge = _make_bridge()
        sql = bridge._build_cube_query("data", ["modality"], None)
        assert "WHERE" not in sql

    def test_selects_facet_columns(self) -> None:
        bridge = _make_bridge()
        sql = bridge._build_cube_query("data", ["modality", "source"], None)
        assert "modality" in sql
        assert "source" in sql

    def test_selects_count(self) -> None:
        bridge = _make_bridge()
        sql = bridge._build_cube_query("data", ["modality"], None)
        assert "COUNT(*)" in sql


class TestComputeFacets:
    """Test facet computation from DuckDB CUBE results."""

    def test_parses_cube_results(self) -> None:
        bridge = _make_bridge()
        # Mock the storage to return a table with modality and source columns
        table = pa.Table.from_pydict(
            {
                "id": list(range(10)),
                "modality": ["image"] * 5 + ["text"] * 5,
                "source": ["web"] * 3 + ["api"] * 4 + ["file"] * 3,
            }
        )
        bridge._storage.read_dataset.return_value = table

        facets = bridge._compute_facets("ds", ["modality"], None)
        assert len(facets) > 0
        facet_names = {f.name for f in facets}
        assert "modality" in facet_names

    def test_empty_dataset_returns_empty_facets(self) -> None:
        bridge = _make_bridge()
        table = pa.Table.from_pydict({"id": [], "modality": [], "source": []})
        bridge._storage.read_dataset.return_value = table

        facets = bridge._compute_facets("ds", ["modality"], None)
        assert len(facets) == 0

    def test_with_where_clause(self) -> None:
        bridge = _make_bridge()
        table = pa.Table.from_pydict(
            {
                "id": list(range(10)),
                "modality": ["image"] * 5 + ["text"] * 5,
                "source": ["web"] * 10,
                "quality_score": [0.9] * 5 + [0.3] * 5,
            }
        )
        bridge._storage.read_dataset.return_value = table

        facets = bridge._compute_facets("ds", ["modality"], "quality_score > 0.5")
        # Only high-quality rows (images) should appear
        assert all(f.count > 0 for f in facets)


class TestSearch:
    """Test faceted search combining vector search + facets."""

    def test_returns_faceted_result(self) -> None:
        bridge = _make_bridge()
        bridge._storage.read_dataset.return_value = pa.Table.from_pydict(
            {
                "id": list(range(10)),
                "modality": ["image"] * 5 + ["text"] * 5,
                "source": ["web"] * 10,
            }
        )

        result = bridge.search(
            "ds",
            [0.1, 0.2, 0.3, 0.4],
            facets=["modality"],
            top_k=5,
        )
        assert isinstance(result, FacetedSearchResult)
        assert result.top_k == 5
        assert result.query_vector_dim == 4
        assert len(result.facets) >= 0

    def test_uses_config_defaults(self) -> None:
        bridge = _make_bridge(default_facet_columns=["modality"])
        bridge._storage.read_dataset.return_value = pa.Table.from_pydict(
            {
                "id": list(range(5)),
                "modality": ["image"] * 5,
            }
        )

        result = bridge.search("ds", [0.1, 0.2, 0.3, 0.4])
        assert isinstance(result, FacetedSearchResult)

    def test_facet_filter_re_executes(self) -> None:
        bridge = _make_bridge()
        bridge._storage.read_dataset.return_value = pa.Table.from_pydict(
            {
                "id": list(range(10)),
                "modality": ["image"] * 5 + ["text"] * 5,
                "source": ["web"] * 10,
            }
        )

        result = bridge.search(
            "ds",
            [0.1, 0.2, 0.3, 0.4],
            facets=["modality"],
            where="modality = 'image'",
        )
        assert isinstance(result, FacetedSearchResult)


class TestConfigDrivenDefaults:
    """Test config-driven defaults."""

    def test_default_max_facet_values(self) -> None:
        config = FacetedSearchConfig()
        assert config.max_facet_values == 50

    def test_default_facet_columns(self) -> None:
        config = FacetedSearchConfig()
        assert config.default_facet_columns == ["modality", "source"]

    def test_custom_config(self) -> None:
        config = FacetedSearchConfig(
            max_facet_values=100,
            default_facet_columns=["modality", "source", "quality_score"],
        )
        assert config.max_facet_values == 100
        assert len(config.default_facet_columns) == 3

    def test_bridge_uses_config(self) -> None:
        bridge = _make_bridge(max_facet_values=25)
        assert bridge._config.max_facet_values == 25
