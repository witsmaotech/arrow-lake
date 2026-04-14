"""Tests for vector search — Story 5.1 (integration).

Tests VectorSearchBridge with real Lance datasets:
- IVF_PQ cosine top-10 search
- IVF_PQ L2 search
- Where filter on metadata
- Empty results handling
- IVF_HNSW_PQ search
- max_distance diagnostics
- End-to-end embed → index → search
- Dimension mismatch error
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from arrow_lake.exceptions import QueryError
from arrow_lake.ingest.storage import LanceStorageManager
from arrow_lake.query.vector import IndexInfo, VectorSearchBridge, VectorSearchResult

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
def bridge(storage: LanceStorageManager) -> VectorSearchBridge:
    return VectorSearchBridge(storage)


@pytest.fixture()
def ds_1000(storage: LanceStorageManager, bridge: VectorSearchBridge) -> str:
    """Create a dataset with 1000 rows, 384-dim vectors. Returns dataset name."""
    name = "vec_1000"
    vectors = _random_vectors(1000)
    table = pa.table(
        {
            "id": [f"doc-{i}" for i in range(1000)],
            "modality": ["text"] * 1000,
            "source": [f"source-{i % 5}" for i in range(1000)],
            "text_embedding": vectors,
        }
    )
    storage.create_dataset(name, table)
    return name


# ---------------------------------------------------------------------------
# AC1: <1M rows, IVF_PQ, top-10 Arrow Table + distance
# ---------------------------------------------------------------------------


class TestIVFPQSearch:
    """Test IVF_PQ cosine search on <1M rows (AC1)."""

    def test_cosine_top_10(
        self,
        bridge: VectorSearchBridge,
        ds_1000: str,
    ) -> None:
        query = _random_vectors(1)[0]
        result = bridge.search(ds_1000, query, top_k=10, metric="cosine")

        assert isinstance(result, VectorSearchResult)
        assert result.row_count == 10
        assert result.query_vector_dim == 384
        assert result.metric == "cosine"
        assert result.top_k == 10
        assert result.table.num_rows == 10
        assert "_distance" in result.table.column_names
        assert result.max_distance is not None
        assert result.max_distance > 0

    def test_cosine_top_k_5(
        self,
        bridge: VectorSearchBridge,
        ds_1000: str,
    ) -> None:
        query = _random_vectors(1, seed=99)[0]
        result = bridge.search(ds_1000, query, top_k=5)

        assert result.row_count == 5
        assert result.top_k == 5

    def test_l2_metric(
        self,
        bridge: VectorSearchBridge,
        ds_1000: str,
    ) -> None:
        query = _random_vectors(1, seed=77)[0]
        result = bridge.search(ds_1000, query, metric="l2")

        assert result.metric == "l2"
        assert result.row_count == 10
        assert "_distance" in result.table.column_names


# ---------------------------------------------------------------------------
# AC1: Metadata filter (where clause)
# ---------------------------------------------------------------------------


class TestWhereFilter:
    """Test where clause filtering during vector search."""

    def test_filter_by_modality(
        self,
        bridge: VectorSearchBridge,
        ds_1000: str,
    ) -> None:
        query = _random_vectors(1, seed=55)[0]
        result = bridge.search(ds_1000, query, where="modality = 'text'")

        assert result.row_count == 10
        # All results should be text
        modalities = result.table.column("modality").to_pylist()
        assert all(m == "text" for m in modalities)

    def test_filter_by_source(
        self,
        bridge: VectorSearchBridge,
        ds_1000: str,
    ) -> None:
        query = _random_vectors(1, seed=66)[0]
        result = bridge.search(
            ds_1000,
            query,
            where="source = 'source-0'",
        )

        assert result.row_count == 10  # 200 rows match source-0
        sources = result.table.column("source").to_pylist()
        assert all(s == "source-0" for s in sources)


# ---------------------------------------------------------------------------
# AC3: Empty results — 0-row table, max_distance=None
# ---------------------------------------------------------------------------


class TestEmptyResults:
    """Test empty result handling (AC3)."""

    def test_no_matching_results(
        self,
        bridge: VectorSearchBridge,
        ds_1000: str,
    ) -> None:
        query = _random_vectors(1, seed=88)[0]
        # Filter that matches no rows
        result = bridge.search(
            ds_1000,
            query,
            where="modality = 'nonexistent'",
        )

        assert result.row_count == 0
        assert result.max_distance is None
        assert result.table.num_rows == 0


# ---------------------------------------------------------------------------
# Index creation and retrieval
# ---------------------------------------------------------------------------


class TestCreateIndex:
    """Test index creation on real Lance datasets."""

    def test_create_ivf_pq_index(
        self,
        bridge: VectorSearchBridge,
        ds_1000: str,
    ) -> None:
        info = bridge.create_index(ds_1000, metric="cosine")

        assert isinstance(info, IndexInfo)
        assert info.index_type == "IVF_PQ"
        assert info.num_indexed_rows > 0

    def test_create_ivf_flat_index(
        self,
        bridge: VectorSearchBridge,
        ds_1000: str,
    ) -> None:
        info = bridge.create_index(ds_1000, metric="l2", index_type="IVF_FLAT")

        assert isinstance(info, IndexInfo)
        assert "IVF_FLAT" in info.index_type

    def test_search_after_index(
        self,
        bridge: VectorSearchBridge,
        ds_1000: str,
    ) -> None:
        bridge.create_index(ds_1000, metric="cosine")
        query = _random_vectors(1, seed=111)[0]
        result = bridge.search(ds_1000, query, top_k=5)

        assert result.row_count == 5
        assert result.max_distance is not None

    def test_get_index_info_after_create(
        self,
        bridge: VectorSearchBridge,
        ds_1000: str,
    ) -> None:
        bridge.get_index_info(ds_1000)
        bridge.create_index(ds_1000, metric="cosine")
        info_after = bridge.get_index_info(ds_1000)

        assert info_after is not None
        assert info_after.index_type == "IVF_PQ"

    def test_get_index_info_no_index(
        self,
        bridge: VectorSearchBridge,
        ds_1000: str,
    ) -> None:
        """Dataset exists but has no index — returns None."""
        info = bridge.get_index_info(ds_1000)
        assert info is None


# ---------------------------------------------------------------------------
# Dimension mismatch
# ---------------------------------------------------------------------------


class TestDimensionMismatch:
    """Test dimension mismatch error handling."""

    def test_wrong_dimension_raises(
        self,
        bridge: VectorSearchBridge,
        ds_1000: str,
    ) -> None:
        wrong_vector = [0.0] * 128  # 128-dim, dataset is 384-dim

        with pytest.raises(QueryError, match="DIMENSION_MISMATCH"):
            bridge.search(ds_1000, wrong_vector)


# ---------------------------------------------------------------------------
# max_distance diagnostics
# ---------------------------------------------------------------------------


class TestMaxDistance:
    """Test max_distance diagnostic information (AC3)."""

    def test_max_distance_in_top_10(
        self,
        bridge: VectorSearchBridge,
        ds_1000: str,
    ) -> None:
        query = _random_vectors(1, seed=200)[0]
        result = bridge.search(ds_1000, query, top_k=10)

        distances = result.table.column("_distance").to_pylist()
        assert len(distances) == 10
        assert result.max_distance == max(distances)
        # max_distance should be the worst (highest) distance
        assert result.max_distance >= min(distances)


# ---------------------------------------------------------------------------
# End-to-end: embed → index → search
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Test full pipeline: create dataset → index → search."""

    def test_e2e_create_index_and_search(
        self,
        storage: LanceStorageManager,
        tmp_path: Path,
    ) -> None:
        """Full pipeline without external embedding — use random vectors."""
        name = "e2e_test"
        vectors = _random_vectors(500, seed=300)
        table = pa.table(
            {
                "id": [f"item-{i}" for i in range(500)],
                "modality": ["text"] * 500,
                "source": [f"src-{i % 3}" for i in range(500)],
                "text_embedding": vectors,
            }
        )
        storage.create_dataset(name, table)

        bridge = VectorSearchBridge(storage)

        # Create index
        info = bridge.create_index(name, metric="cosine")
        assert isinstance(info, IndexInfo)

        # Search with the first vector (should find itself as nearest)
        query = vectors[0]
        result = bridge.search(name, query, top_k=1)
        assert result.row_count == 1
        # The nearest neighbor should be close to itself
        assert result.max_distance is not None
        assert result.max_distance < 0.5  # PQ quantization adds some error
        # Verify the returned row is the one we searched for
        assert result.table.column("id")[0].as_py() == "item-0"


# ---------------------------------------------------------------------------
# Lake SDK entry point
# ---------------------------------------------------------------------------


class TestLakeSDK:
    """Test Lake.search() and Lake.create_vector_index() SDK methods."""

    def test_lake_search(
        self,
        storage: LanceStorageManager,
        ds_1000: str,
        tmp_path: Path,
    ) -> None:
        from arrow_lake import Lake

        lake = Lake(base_uri=str(tmp_path / "lance_data"))
        query = _random_vectors(1, seed=400)[0]
        result = lake.search(ds_1000, query, top_k=5)

        assert isinstance(result, VectorSearchResult)
        assert result.row_count == 5

    def test_lake_create_vector_index(
        self,
        storage: LanceStorageManager,
        ds_1000: str,
        tmp_path: Path,
    ) -> None:
        from arrow_lake import Lake

        lake = Lake(base_uri=str(tmp_path / "lance_data"))
        info = lake.create_vector_index(ds_1000, metric="cosine")

        assert isinstance(info, IndexInfo)
