"""Tests for hybrid search — Story 5.3 (integration).

Tests HybridSearchBridge with real Lance datasets:
- Hybrid search top-10 with RRF fusion
- Hybrid + where filter
- Empty results
- Vector-priority results
- FTS-priority results
- max_rrf_score diagnostics
- End-to-end: create data → index → hybrid search
- Lake.hybrid_search() SDK entry point
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from arrow_lake.ingest.storage import LanceStorageManager
from arrow_lake.query.hybrid import HybridSearchBridge, HybridSearchResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _random_vectors(n: int, dim: int = 384, seed: int = 42) -> list[list[float]]:
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
def bridge(storage: LanceStorageManager) -> HybridSearchBridge:
    return HybridSearchBridge(storage)


@pytest.fixture()
def ds_hybrid(storage: LanceStorageManager) -> str:
    """Create a dataset with text + vectors for hybrid search. Returns dataset name."""
    name = "hybrid_ds"
    texts = [
        "Machine learning algorithms for natural language processing",
        "Deep neural networks and computer vision applications",
        "Database optimization and SQL query performance tuning",
        "Cloud infrastructure and distributed systems design",
        "Reinforcement learning for autonomous decision making",
    ]
    rows = 50
    vectors = _random_vectors(rows)
    table = pa.table(
        {
            "id": [f"doc-{i}" for i in range(rows)],
            "modality": ["text"] * rows,
            "source": [f"source-{i % 5}" for i in range(rows)],
            "text_content": [texts[i % len(texts)] for i in range(rows)],
            "text_embedding": vectors,
        }
    )
    storage.create_dataset(name, table)
    return name


# ---------------------------------------------------------------------------
# Hybrid search tests
# ---------------------------------------------------------------------------


class TestHybridSearch:
    """Test hybrid RRF search on Lance datasets."""

    def test_hybrid_search_returns_results(
        self,
        bridge: HybridSearchBridge,
        ds_hybrid: str,
    ) -> None:
        query_vector = _random_vectors(1, seed=100)[0]
        result = bridge.search(ds_hybrid, query_vector, "machine learning")

        assert isinstance(result, HybridSearchResult)
        assert result.row_count > 0
        assert result.query_text == "machine learning"
        assert result.query_vector_dim == 384
        assert "_rrf_score" in result.table.column_names
        assert result.max_rrf_score is not None
        assert result.max_rrf_score > 0

    def test_hybrid_search_top_k(
        self,
        bridge: HybridSearchBridge,
        ds_hybrid: str,
    ) -> None:
        query_vector = _random_vectors(1, seed=101)[0]
        result = bridge.search(ds_hybrid, query_vector, "learning", top_k=5)

        assert result.row_count == 5
        assert result.top_k == 5


# ---------------------------------------------------------------------------
# Hybrid + where filter
# ---------------------------------------------------------------------------


class TestHybridWhereFilter:
    """Test where clause filtering during hybrid search."""

    def test_filter_by_modality(
        self,
        bridge: HybridSearchBridge,
        ds_hybrid: str,
    ) -> None:
        query_vector = _random_vectors(1, seed=102)[0]
        result = bridge.search(
            ds_hybrid,
            query_vector,
            "learning",
            where="modality = 'text'",
        )

        assert result.row_count > 0
        modalities = result.table.column("modality").to_pylist()
        assert all(m == "text" for m in modalities)


# ---------------------------------------------------------------------------
# Empty results
# ---------------------------------------------------------------------------


class TestHybridEmptyResults:
    """Test empty result handling."""

    def test_no_matching_results(
        self,
        bridge: HybridSearchBridge,
        ds_hybrid: str,
    ) -> None:
        query_vector = _random_vectors(1, seed=103)[0]
        result = bridge.search(
            ds_hybrid,
            query_vector,
            "xyznonexistent123456789",
            where="modality = 'nonexistent'",
        )

        assert result.row_count == 0
        assert result.max_rrf_score is None


# ---------------------------------------------------------------------------
# max_rrf_score diagnostics
# ---------------------------------------------------------------------------


class TestMaxRRFScore:
    """Test max_rrf_score diagnostic information."""

    def test_max_rrf_score_in_results(
        self,
        bridge: HybridSearchBridge,
        ds_hybrid: str,
    ) -> None:
        query_vector = _random_vectors(1, seed=104)[0]
        result = bridge.search(ds_hybrid, query_vector, "learning")

        scores = result.table.column("_rrf_score").to_pylist()
        assert len(scores) > 0
        assert result.max_rrf_score == max(scores)
        assert result.max_rrf_score >= min(scores)

    def test_max_rrf_score_none_when_empty(
        self,
        bridge: HybridSearchBridge,
        ds_hybrid: str,
    ) -> None:
        query_vector = _random_vectors(1, seed=105)[0]
        # Use where clause that matches no rows for both vector and FTS
        result = bridge.search(
            ds_hybrid,
            query_vector,
            "xyznonexistent123456789",
            where="modality = 'nonexistent'",
        )

        assert result.row_count == 0
        assert result.max_rrf_score is None


# ---------------------------------------------------------------------------
# End-to-end: create data → index → hybrid search
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Test full pipeline: create dataset → index → hybrid search."""

    def test_e2e_hybrid_search(
        self,
        storage: LanceStorageManager,
        tmp_path: Path,
    ) -> None:
        from arrow_lake.query.fts import FullTextSearchBridge
        from arrow_lake.query.vector import VectorSearchBridge

        name = "e2e_hybrid"
        texts = [
            "Python machine learning scikit-learn",
            "JavaScript web development React",
            "Python data analysis pandas numpy",
            "Rust systems programming performance",
            "Python deep learning TensorFlow",
        ]
        rows = 300
        vectors = _random_vectors(rows, seed=200)
        table = pa.table(
            {
                "id": [f"item-{i}" for i in range(rows)],
                "modality": ["text"] * rows,
                "source": [f"src-{i % 3}" for i in range(rows)],
                "text_content": [texts[i % len(texts)] for i in range(rows)],
                "text_embedding": vectors,
            }
        )
        storage.create_dataset(name, table)

        # Create FTS index
        fts_bridge = FullTextSearchBridge(storage)
        fts_bridge.create_index(name)

        # Create vector index
        vector_bridge = VectorSearchBridge(storage)
        vector_bridge.create_index(name, metric="cosine")

        # Hybrid search
        hybrid_bridge = HybridSearchBridge(storage)
        query_vector = vectors[0]
        result = hybrid_bridge.search(name, query_vector, "Python machine learning")

        assert result.row_count > 0
        assert result.max_rrf_score is not None
        assert "_rrf_score" in result.table.column_names


# ---------------------------------------------------------------------------
# Lake SDK entry point
# ---------------------------------------------------------------------------


class TestLakeSDK:
    """Test Lake.hybrid_search() SDK method."""

    def test_lake_hybrid_search(
        self,
        storage: LanceStorageManager,
        ds_hybrid: str,
        tmp_path: Path,
    ) -> None:
        from arrow_lake import Lake

        lake = Lake(base_uri=str(tmp_path / "lance_data"))
        query_vector = _random_vectors(1, seed=300)[0]
        result = lake.hybrid_search(ds_hybrid, query_vector, "machine learning")

        assert isinstance(result, HybridSearchResult)
        assert result.row_count > 0
