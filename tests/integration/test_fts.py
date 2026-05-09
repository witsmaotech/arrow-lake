"""Tests for full-text search — Story 5.2 (integration).

Tests FullTextSearchBridge with real Lance datasets:
- FTS search top-10
- FTS + where filter
- FTS empty results
- FTS index creation and search
- End-to-end: create data → index → search
- max_score diagnostics
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.ingest.storage import LanceStorageManager
from arrow_lake.query.fts import FullTextSearchBridge, FullTextSearchResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def storage(tmp_path: Path) -> LanceStorageManager:
    return LanceStorageManager(str(tmp_path / "lance_data"))


@pytest.fixture()
def bridge(storage: LanceStorageManager) -> FullTextSearchBridge:
    return FullTextSearchBridge(storage)


@pytest.fixture()
def ds_texts(storage: LanceStorageManager) -> str:
    """Create a dataset with 100 text rows. Returns dataset name."""
    name = "fts_texts"
    texts = [
        "Machine learning is a subset of artificial intelligence",
        "Deep learning uses neural networks with many layers",
        "Natural language processing enables computers to understand text",
        "Computer vision deals with image and video analysis",
        "Reinforcement learning trains agents through rewards",
        "Transfer learning leverages pre-trained models",
        "Data augmentation increases training dataset diversity",
        "Gradient descent optimizes model parameters iteratively",
        "Overfitting occurs when a model memorizes training data",
        "Cross-validation helps assess model generalization performance",
    ]
    # Create 100 rows by cycling through 10 texts
    rows = 100
    # Include text_embedding column to match UNIFIED_SCHEMA (required by LanceDB search API)
    table = pa.table(
        {
            "id": [f"doc-{i}" for i in range(rows)],
            "modality": ["text"] * rows,
            "source": [f"source-{i % 5}" for i in range(rows)],
            "text_content": [texts[i % len(texts)] for i in range(rows)],
            "text_embedding": [[0.0] * 384 for _ in range(rows)],
        }
    )
    storage.create_dataset(name, table)
    return name


@pytest.fixture()
def ds_indexed(
    storage: LanceStorageManager,
    bridge: FullTextSearchBridge,
    ds_texts: str,
) -> str:
    """Create FTS index on ds_texts. Returns dataset name."""
    bridge.create_index(ds_texts)
    return ds_texts


# ---------------------------------------------------------------------------
# FTS search tests
# ---------------------------------------------------------------------------


class TestFTSSearch:
    """Test FTS search on Lance datasets."""

    def test_search_returns_results(
        self,
        bridge: FullTextSearchBridge,
        ds_texts: str,
    ) -> None:
        result = bridge.search(ds_texts, "machine learning")

        assert isinstance(result, FullTextSearchResult)
        assert result.row_count > 0
        assert result.query == "machine learning"
        assert result.fts_column == "text_content"
        assert "_score" in result.table.column_names
        assert result.max_score is not None
        assert result.max_score > 0

    def test_search_top_10(
        self,
        bridge: FullTextSearchBridge,
        ds_texts: str,
    ) -> None:
        result = bridge.search(ds_texts, "learning")

        assert result.row_count == 10
        assert result.top_k == 10

    def test_search_custom_top_k(
        self,
        bridge: FullTextSearchBridge,
        ds_texts: str,
    ) -> None:
        result = bridge.search(ds_texts, "learning", top_k=5)

        assert result.row_count == 5
        assert result.top_k == 5


# ---------------------------------------------------------------------------
# FTS + where filter
# ---------------------------------------------------------------------------


class TestFTSWhereFilter:
    """Test where clause filtering during FTS search."""

    def test_filter_by_modality(
        self,
        bridge: FullTextSearchBridge,
        ds_texts: str,
    ) -> None:
        result = bridge.search(ds_texts, "learning", where="modality = 'text'")

        assert result.row_count > 0
        modalities = result.table.column("modality").to_pylist()
        assert all(m == "text" for m in modalities)

    def test_filter_by_source(
        self,
        bridge: FullTextSearchBridge,
        ds_texts: str,
    ) -> None:
        result = bridge.search(ds_texts, "learning", where="source = 'source-0'")

        assert result.row_count > 0
        sources = result.table.column("source").to_pylist()
        assert all(s == "source-0" for s in sources)


# ---------------------------------------------------------------------------
# FTS empty results
# ---------------------------------------------------------------------------


class TestFTSEmptyResults:
    """Test empty result handling."""

    def test_no_matching_results(
        self,
        bridge: FullTextSearchBridge,
        ds_texts: str,
    ) -> None:
        result = bridge.search(ds_texts, "xyznonexistent123456789")

        assert result.row_count == 0
        assert result.max_score is None
        assert result.table.num_rows == 0

    def test_no_matching_filter(
        self,
        bridge: FullTextSearchBridge,
        ds_texts: str,
    ) -> None:
        result = bridge.search(
            ds_texts,
            "learning",
            where="modality = 'nonexistent'",
        )

        assert result.row_count == 0
        assert result.max_score is None


# ---------------------------------------------------------------------------
# FTS index creation and search
# ---------------------------------------------------------------------------


class TestFTSCreateIndex:
    """Test FTS index creation on real Lance datasets."""

    def test_create_index_success(
        self,
        bridge: FullTextSearchBridge,
        ds_texts: str,
    ) -> None:
        # Should not raise
        bridge.create_index(ds_texts)

    def test_search_after_index(
        self,
        bridge: FullTextSearchBridge,
        ds_texts: str,
    ) -> None:
        bridge.create_index(ds_texts)
        result = bridge.search(ds_texts, "neural networks")

        assert result.row_count > 0
        assert result.max_score is not None

    def test_create_index_replace(
        self,
        bridge: FullTextSearchBridge,
        ds_texts: str,
    ) -> None:
        bridge.create_index(ds_texts)
        # Replace should not raise
        bridge.create_index(ds_texts, replace=True)


# ---------------------------------------------------------------------------
# max_score diagnostics
# ---------------------------------------------------------------------------


class TestMaxScore:
    """Test max_score diagnostic information."""

    def test_max_score_in_results(
        self,
        bridge: FullTextSearchBridge,
        ds_texts: str,
    ) -> None:
        result = bridge.search(ds_texts, "learning")

        scores = result.table.column("_score").to_pylist()
        assert len(scores) > 0
        assert result.max_score == max(scores)
        assert result.max_score >= min(scores)

    def test_max_score_none_when_empty(
        self,
        bridge: FullTextSearchBridge,
        ds_texts: str,
    ) -> None:
        result = bridge.search(ds_texts, "xyznonexistent123456789")

        assert result.max_score is None


# ---------------------------------------------------------------------------
# End-to-end: create data → index → search
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Test full pipeline: create dataset → index → search."""

    def test_e2e_create_index_and_search(
        self,
        storage: LanceStorageManager,
        tmp_path: Path,
    ) -> None:
        name = "e2e_fts"
        texts = [
            "The quick brown fox jumps over the lazy dog",
            "Artificial intelligence transforms software engineering",
            "Distributed systems require careful consistency management",
            "Cloud computing provides scalable infrastructure",
            "Database optimization requires understanding query patterns",
        ]
        rows = 50
        table = pa.table(
            {
                "id": [f"item-{i}" for i in range(rows)],
                "modality": ["text"] * rows,
                "source": [f"src-{i % 3}" for i in range(rows)],
                "text_content": [texts[i % len(texts)] for i in range(rows)],
                "text_embedding": [[0.0] * 384 for _ in range(rows)],
            }
        )
        storage.create_dataset(name, table)

        bridge = FullTextSearchBridge(storage)

        # Create index
        bridge.create_index(name)

        # Search
        result = bridge.search(name, "database optimization")
        assert result.row_count > 0
        assert result.max_score is not None


# ---------------------------------------------------------------------------
# Lake SDK entry point
# ---------------------------------------------------------------------------


class TestLakeSDK:
    """Test Lake.text_search() and Lake.create_fts_index() SDK methods."""

    def test_lake_text_search(
        self,
        storage: LanceStorageManager,
        ds_texts: str,
        tmp_path: Path,
    ) -> None:
        from arrow_lake import Lake
        from arrow_lake.config import ArrowLakeConfig, StorageConfig

        lake = Lake(base_uri=str(tmp_path / "lance_data"), config=ArrowLakeConfig(storage=StorageConfig(backend="local")))
        result = lake.text_search(ds_texts, "machine learning")

        assert isinstance(result, FullTextSearchResult)
        assert result.row_count > 0

    def test_lake_create_fts_index(
        self,
        storage: LanceStorageManager,
        ds_texts: str,
        tmp_path: Path,
    ) -> None:
        from arrow_lake import Lake
        from arrow_lake.config import ArrowLakeConfig, StorageConfig

        lake = Lake(base_uri=str(tmp_path / "lance_data"), config=ArrowLakeConfig(storage=StorageConfig(backend="local")))
        # Should not raise
        lake.create_fts_index(ds_texts)
