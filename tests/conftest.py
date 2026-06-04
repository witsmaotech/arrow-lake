"""Shared test fixtures for the Arrow Lake test suite.

Provides reusable fixtures for Lance datasets, DuckDB sessions,
PyArrow test tables, and storage managers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def pytest_configure(config):
    """Register custom pytest marks."""
    config.addinivalue_line("markers", "spike: spike/benchmark tests (require external services)")


# ---------------------------------------------------------------------------
# Lance dataset fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def lance_tmp_dir(tmp_path: Path) -> str:
    """Provide a temporary directory for Lance datasets."""
    return str(tmp_path)


@pytest.fixture()
def sample_table():
    """Create a small sample Arrow table with text + vector columns."""
    import numpy as np
    import pyarrow as pa

    rng = np.random.RandomState(42)
    dim = 128
    vectors = rng.randn(10, dim).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    vectors = vectors / norms

    return pa.table(
        {
            "id": [f"doc_{i:03d}" for i in range(10)],
            "text_content": [f"Sample document {i} about machine learning" for i in range(10)],
            "modality": ["text"] * 10,
            "source": ["test"] * 10,
            "vector": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
        }
    )


@pytest.fixture()
def sample_vector_table():
    """Create a minimal table with only id + vector columns."""
    import numpy as np
    import pyarrow as pa

    rng = np.random.RandomState(42)
    dim = 64
    vectors = rng.randn(5, dim).astype(np.float32)

    return pa.table(
        {
            "id": [f"vec_{i:03d}" for i in range(5)],
            "vector": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
        }
    )


@pytest.fixture()
def storage(lance_tmp_dir: str) -> Any:
    """Provide a LanceStorageManager with a temporary directory."""
    from arrow_lake.ingest.storage import LanceStorageManager

    return LanceStorageManager(base_uri=lance_tmp_dir)


# ---------------------------------------------------------------------------
# DuckDB session fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def duckdb_session() -> Any:
    """Provide a managed DuckDB session with lance extension loaded."""
    from arrow_lake.query._db import DuckDBSession

    with DuckDBSession() as conn:
        yield conn


# ---------------------------------------------------------------------------
# Lance scan adapter fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def lance_scan_adapter(duckdb_session: Any) -> Any:
    """Provide a LanceScanAdapter (auto mode) with an active connection."""
    from arrow_lake.query.lance_adapter import create_lance_scan_adapter

    return create_lance_scan_adapter(duckdb_session, mode="auto", dataset=None)


# ---------------------------------------------------------------------------
# Dataset creation helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_table():
    """Create a test table factory with vector + text columns."""
    import numpy as np
    import pyarrow as pa

    def _make(n: int = 100, dim: int = 128) -> pa.Table:
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
