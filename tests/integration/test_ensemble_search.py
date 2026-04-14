"""Integration tests for multi-model ensemble search — Story 8.2.

Tests ensemble search across multiple embedding columns with real
Lance datasets.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from arrow_lake.ingest.storage import LanceStorageManager
from arrow_lake.query.ensemble import (
    EnsembleSearchBridge,
    EnsembleSearchConfig,
    EnsembleSearchResult,
)

EMBEDDING_DIM = 128


def _random_vectors(n: int, dim: int = EMBEDDING_DIM, seed: int = 42) -> list[list[float]]:
    """Generate n random unit-norm vectors."""
    rng = np.random.default_rng(seed)
    vecs = rng.standard_normal((n, dim)).astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / norms
    return vecs.tolist()


@pytest.fixture()
def storage(tmp_path: Path) -> LanceStorageManager:
    return LanceStorageManager(str(tmp_path / "lance_data"))


@pytest.fixture()
def dual_embedding_dataset(storage: LanceStorageManager) -> str:
    """Create a dataset with text_embedding and image_embedding (both 128d)."""
    name = "ensemble_ds"
    n = 200
    np.random.default_rng(42)

    table = pa.table(
        {
            "id": [f"doc-{i}" for i in range(n)],
            "modality": ["text"] * n,
            "text_embedding": _random_vectors(n, seed=42),
            "image_embedding": _random_vectors(n, seed=99),
        }
    )
    storage.create_dataset(name, table)
    return name


@pytest.fixture()
def empty_dataset(storage: LanceStorageManager) -> str:
    """Create an empty dataset with embedding columns."""
    name = "empty_ensemble"
    table = pa.table(
        {
            "id": pa.array([], type=pa.string()),
            "text_embedding": pa.array([], type=pa.list_(pa.float32(), EMBEDDING_DIM)),
            "image_embedding": pa.array([], type=pa.list_(pa.float32(), EMBEDDING_DIM)),
        }
    )
    storage.create_dataset(name, table)
    return name


class TestSingleColumnEnsemble:
    """Test ensemble search on a single embedding column."""

    def test_single_column_ensemble(
        self,
        storage: LanceStorageManager,
        dual_embedding_dataset: str,
    ) -> None:
        """Search with columns=['text_embedding'], verify results returned."""
        bridge = EnsembleSearchBridge(storage)
        query = _random_vectors(1)[0]

        result = bridge.search(
            dual_embedding_dataset,
            query,
            columns=["text_embedding"],
            top_k=5,
        )

        assert isinstance(result, EnsembleSearchResult)
        assert result.row_count == 5
        assert result.columns_searched == ("text_embedding",)
        assert result.top_k == 5
        assert result.query_vector_dim == EMBEDDING_DIM
        assert result.table.num_rows == 5


class TestMultiColumnEnsemble:
    """Test ensemble search across multiple embedding columns."""

    def test_multi_column_ensemble(
        self,
        storage: LanceStorageManager,
        dual_embedding_dataset: str,
    ) -> None:
        """Search with both columns, verify fusion produces _ensemble_score."""
        bridge = EnsembleSearchBridge(storage)
        query = _random_vectors(1)[0]

        result = bridge.search(
            dual_embedding_dataset,
            query,
            columns=["text_embedding", "image_embedding"],
            top_k=10,
        )

        assert result.row_count == 10
        assert result.columns_searched == ("text_embedding", "image_embedding")
        assert result.fusion_method == "rrf"
        assert "_ensemble_score" in result.table.column_names


class TestWeightedEnsemble:
    """Test weighted ensemble search produces different scores."""

    def test_weighted_ensemble(
        self,
        storage: LanceStorageManager,
        dual_embedding_dataset: str,
    ) -> None:
        """Search with weights, verify scores differ from unweighted."""
        bridge = EnsembleSearchBridge(storage)
        query = _random_vectors(1)[0]

        unweighted = bridge.search(
            dual_embedding_dataset,
            query,
            columns=["text_embedding", "image_embedding"],
            top_k=10,
        )

        weighted = bridge.search(
            dual_embedding_dataset,
            query,
            columns=["text_embedding", "image_embedding"],
            weights={"text_embedding": 2.0, "image_embedding": 0.5},
            top_k=10,
        )

        # Both should return results
        assert unweighted.row_count == 10
        assert weighted.row_count == 10

        # Scores should differ between weighted and unweighted
        uw_scores = unweighted.table.column("_ensemble_score").to_pylist()
        w_scores = weighted.table.column("_ensemble_score").to_pylist()
        assert uw_scores != w_scores


class TestAutoDetectColumns:
    """Test auto-detection of embedding columns."""

    def test_auto_detect_columns(
        self,
        storage: LanceStorageManager,
        dual_embedding_dataset: str,
    ) -> None:
        """Search with columns=None, verify auto-detection picks up both vector columns."""
        bridge = EnsembleSearchBridge(storage)
        query = _random_vectors(1)[0]

        result = bridge.search(
            dual_embedding_dataset,
            query,
            columns=None,
            top_k=5,
        )

        assert len(result.columns_searched) == 2
        assert "text_embedding" in result.columns_searched
        assert "image_embedding" in result.columns_searched


class TestResultMetadata:
    """Test EnsembleSearchResult metadata fields."""

    def test_result_metadata(
        self,
        storage: LanceStorageManager,
        dual_embedding_dataset: str,
    ) -> None:
        """Verify EnsembleSearchResult has correct metadata."""
        bridge = EnsembleSearchBridge(
            storage,
            config=EnsembleSearchConfig(default_top_k=7, rrf_k=42),
        )
        query = _random_vectors(1)[0]

        result = bridge.search(
            dual_embedding_dataset,
            query,
            columns=["text_embedding", "image_embedding"],
        )

        assert result.columns_searched == ("text_embedding", "image_embedding")
        assert result.fusion_method == "rrf"
        assert result.top_k == 7
        assert result.query_vector_dim == EMBEDDING_DIM
        assert result.row_count == 7


class TestEmptyDataset:
    """Test ensemble search on empty dataset."""

    def test_empty_dataset(
        self,
        storage: LanceStorageManager,
        empty_dataset: str,
    ) -> None:
        """Search on empty dataset returns empty results."""
        bridge = EnsembleSearchBridge(storage)
        query = _random_vectors(1)[0]

        result = bridge.search(
            empty_dataset,
            query,
            columns=["text_embedding"],
            top_k=5,
        )

        assert result.row_count == 0
        assert result.table.num_rows == 0
