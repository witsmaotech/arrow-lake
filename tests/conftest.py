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


# ---------------------------------------------------------------------------
# Global state isolation (v1.9.2 批3)
# ---------------------------------------------------------------------------
# Root cause of full-suite flakiness (memory issue_test_isolation_pollution):
# tests/conftest.py had NO autouse global-state reset, so module-level caches
# / process singletons / DuckDB handles leaked across tests. Individual files
# pass alone but fail under the full run (~106 failed, wave-like).
#
# This autouse fixture runs after every test and clears the known process-level
# caches defensively (each clear is wrapped so a missing/renamed attribute can
# never introduce a NEW failure). gc.collect() at the end reclaims lingering
# cyclic refs held by module singletons.


@pytest.fixture(autouse=True)
def reset_global_state():
    """Function-scope teardown: clear process-level caches/singletons.

    Best-effort — every clear is guarded so unrelated tests are never affected.
    """
    yield

    import gc

    # arrow_lake.ingest.document: parse cache + docling converter cache
    # (confirmed module-level mutables; see document.py:198/226).
    try:
        from arrow_lake.ingest import document as _doc

        _doc._PARSE_CACHE.clear()  # type: ignore[attr-defined]
        _doc._DOCLING_CONVERTERS.clear()  # type: ignore[attr-defined]
    except Exception:
        pass

    # DuckDB: some adapters keep a module-level connection pool. Clear any dict
    # / list named like a session pool if present.
    for _mod_path in (
        "arrow_lake.query._db",
        "arrow_lake.query.lance_adapter",
    ):
        try:
            import importlib

            _mod = importlib.import_module(_mod_path)
            for _pool_name in (
                "_GLOBAL_SESSIONS",
                "_SESSIONS",
                "_POOL",
                "_global_pool",
            ):
                _pool = getattr(_mod, _pool_name, None)
                if isinstance(_pool, dict):
                    _pool.clear()
                elif isinstance(_pool, list):
                    _pool.clear()
        except Exception:
            pass

    gc.collect()
