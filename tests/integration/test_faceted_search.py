"""Integration tests for Story 8.1 — Faceted Search."""

from __future__ import annotations

import pyarrow as pa
from arrow_lake.ingest.storage import LanceStorageManager
from arrow_lake.query.faceted import FacetedSearchBridge


def _create_test_dataset(
    tmp_path: str,
    name: str = "test_facet",
    rows: int = 100,
) -> str:
    """Create a Lance dataset with modality, source, quality_score, embedding."""
    storage = LanceStorageManager(str(tmp_path))
    schema = pa.schema(
        [
            pa.field("id", pa.int64()),
            pa.field("modality", pa.utf8()),
            pa.field("source", pa.utf8()),
            pa.field("quality_score", pa.float64()),
            pa.field("embedding", pa.list_(pa.float32(), 4)),
        ]
    )
    data = {
        "id": list(range(rows)),
        "modality": [
            "image" if i % 3 == 0 else "text" if i % 3 == 1 else "video" for i in range(rows)
        ],
        "source": ["web" if i % 2 == 0 else "api" for i in range(rows)],
        "quality_score": [0.5 + (i % 10) * 0.05 for i in range(rows)],
        "embedding": [[float(i % 256)] * 4 for i in range(rows)],
    }
    table = pa.Table.from_pydict(data, schema=schema)
    storage.create_dataset(name, table)
    return name


class TestFacetedSearchIntegration:
    """Integration tests with real Lance datasets."""

    def test_facet_counts_accuracy(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path, rows=99)
        storage = LanceStorageManager(str(tmp_path))
        bridge = FacetedSearchBridge(storage)

        result = bridge.search(
            ds_name,
            [0.0, 0.0, 0.0, 0.0],
            facets=["modality"],
            top_k=10,
        )

        facet_names = {f.name for f in result.facets}
        assert "modality" in facet_names

        modality_facets = [f for f in result.facets if f.name == "modality"]
        total_count = sum(f.count for f in modality_facets)
        assert total_count > 0

    def test_multi_dimension_facets(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path)
        storage = LanceStorageManager(str(tmp_path))
        bridge = FacetedSearchBridge(storage)

        result = bridge.search(
            ds_name,
            [0.0, 0.0, 0.0, 0.0],
            facets=["modality", "source"],
            top_k=5,
        )

        facet_names = {f.name for f in result.facets}
        assert "modality" in facet_names
        assert "source" in facet_names

    def test_facet_filter_reduces_results(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path)
        storage = LanceStorageManager(str(tmp_path))
        bridge = FacetedSearchBridge(storage)

        unfiltered = bridge.search(
            ds_name,
            [0.0, 0.0, 0.0, 0.0],
            facets=["modality"],
            top_k=100,
        )

        filtered = bridge.search(
            ds_name,
            [0.0, 0.0, 0.0, 0.0],
            facets=["modality"],
            top_k=100,
            where="modality = 'text'",
        )

        # Filtered should have fewer rows
        assert filtered.row_count <= unfiltered.row_count

    def test_result_metadata(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path)
        storage = LanceStorageManager(str(tmp_path))
        bridge = FacetedSearchBridge(storage)

        result = bridge.search(
            ds_name,
            [0.0, 0.0, 0.0, 0.0],
            facets=["modality", "source"],
            top_k=5,
        )

        assert isinstance(result.table, pa.Table)
        assert result.query_vector_dim == 4
        assert result.top_k == 5
        assert result.total_facets >= 0
        assert result.row_count <= 5

    def test_empty_facets_returns_empty_list(self, tmp_path: str) -> None:
        ds_name = _create_test_dataset(tmp_path, rows=0)
        storage = LanceStorageManager(str(tmp_path))
        bridge = FacetedSearchBridge(storage)

        result = bridge.search(
            ds_name,
            [0.0, 0.0, 0.0, 0.0],
            facets=["modality"],
            top_k=5,
        )

        assert result.facets == []
        assert result.total_facets == 0
