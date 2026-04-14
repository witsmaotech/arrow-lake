"""Shared fixtures for benchmark tests — Story 5.8."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest


@pytest.fixture()
def lance_tmp_dir(tmp_path: object) -> str:
    """Provide a temporary directory for Lance datasets."""
    return str(tmp_path)


@pytest.fixture()
def make_table() -> pa.Table:
    """Create a test table factory with vector + text columns."""

    def _make(n: int = 1000, dim: int = 128) -> pa.Table:
        rng = np.random.RandomState(42)
        vectors = rng.randn(n, dim).astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        vectors = vectors / norms
        return pa.table(
            {
                "id": [f"doc_{i:06d}" for i in range(n)],
                "text_content": [
                    f"Document number {i} about machine learning and data processing"
                    for i in range(n)
                ],
                "vector": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
            }
        )

    return _make


@pytest.fixture()
def make_indexed_table(lance_tmp_dir: str) -> object:
    """Create a Lance dataset with IVF_PQ vector index."""
    from arrow_lake.ingest.storage import LanceStorageManager
    from arrow_lake.query.vector import VectorSearchBridge

    storage = LanceStorageManager(base_uri=lance_tmp_dir)

    def _make(name: str = "bench_vectors", n: int = 1000, dim: int = 128) -> None:
        rng = np.random.RandomState(42)
        vectors = rng.randn(n, dim).astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        vectors = vectors / norms
        table = pa.table(
            {
                "id": [f"doc_{i:06d}" for i in range(n)],
                "text_content": [
                    f"Document number {i} about machine learning and data processing"
                    for i in range(n)
                ],
                "vector": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
            }
        )
        storage.create_dataset(name, table)
        bridge = VectorSearchBridge(storage)
        bridge.create_index(name, vector_column="vector")

    return _make


@pytest.fixture()
def make_fts_indexed_table(lance_tmp_dir: str) -> object:
    """Create a Lance dataset with FTS index."""
    from arrow_lake.ingest.storage import LanceStorageManager
    from arrow_lake.query.fts import FullTextSearchBridge

    storage = LanceStorageManager(base_uri=lance_tmp_dir)

    def _make(name: str = "bench_fts", n: int = 1000) -> None:
        table = pa.table(
            {
                "id": [f"doc_{i:06d}" for i in range(n)],
                "text_content": [
                    f"Document number {i} about machine learning and data processing"
                    for i in range(n)
                ],
            }
        )
        storage.create_dataset(name, table)
        bridge = FullTextSearchBridge(storage)
        bridge.create_index(name)

    return _make
