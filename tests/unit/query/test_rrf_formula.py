"""Tests for RRF formula correctness and stability (v1.6.0 Phase 1).

Validates:
- RRF uses standard formula: 1/(rank + k) with rank starting at 1 (Cormack et al., 2009)
- Equal-score documents maintain stable sort order
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from arrow_lake.query.hybrid import HybridSearchBridge


def _make_vector_table(ids: list[str]) -> pa.Table:
    return pa.table(
        {
            "id": ids,
            "modality": ["text"] * len(ids),
            "source": ["src-0"] * len(ids),
            "_distance": [0.1 * (i + 1) for i in range(len(ids))],
        }
    )


def _make_fts_table(ids: list[str]) -> pa.Table:
    return pa.table(
        {
            "id": ids,
            "modality": ["text"] * len(ids),
            "source": ["src-0"] * len(ids),
            "_score": [2.0 - 0.2 * i for i in range(len(ids))],
        }
    )


class TestRRFFormula:
    """Verify RRF formula matches the standard definition."""

    def test_rank_starts_at_one(self) -> None:
        """Single doc in vector only: score = 1/(1 + k)."""
        v_table = _make_vector_table(["doc-1"])
        f_table = _make_fts_table([])

        result = HybridSearchBridge._rrf_fuse(v_table, f_table, k=60, top_k=10)
        score = result.column("_rrf_score")[0].as_py()
        expected = 1.0 / (1 + 60)  # rank=1, k=60
        assert abs(score - expected) < 1e-6

    def test_second_rank_score(self) -> None:
        """Two docs in vector: second gets 1/(2 + k)."""
        v_table = _make_vector_table(["doc-1", "doc-2"])
        f_table = _make_fts_table([])

        result = HybridSearchBridge._rrf_fuse(v_table, f_table, k=60, top_k=10)
        scores = result.column("_rrf_score").to_pylist()
        expected_rank2 = 1.0 / (2 + 60)
        ids = result.column("id").to_pylist()
        doc2_idx = ids.index("doc-2")
        assert abs(scores[doc2_idx] - expected_rank2) < 1e-6

    def test_overlap_doubles_score(self) -> None:
        """Doc in both vector (rank 1) and FTS (rank 1) gets 2 * 1/(1+k)."""
        v_table = _make_vector_table(["doc-1"])
        f_table = _make_fts_table(["doc-1"])

        result = HybridSearchBridge._rrf_fuse(v_table, f_table, k=60, top_k=10)
        score = result.column("_rrf_score")[0].as_py()
        expected = 2.0 * (1.0 / (1 + 60))
        assert abs(score - expected) < 1e-6

    def test_k_equals_one(self) -> None:
        """With k=1: score = 1/(rank + 1)."""
        v_table = _make_vector_table(["doc-1", "doc-2", "doc-3"])
        f_table = _make_fts_table([])

        result = HybridSearchBridge._rrf_fuse(v_table, f_table, k=1, top_k=10)
        scores = {row["id"]: row["_rrf_score"] for row in result.to_pylist()}

        assert abs(scores["doc-1"] - 1.0 / (1 + 1)) < 1e-6  # 0.5
        assert abs(scores["doc-2"] - 1.0 / (2 + 1)) < 1e-6  # 0.333...
        assert abs(scores["doc-3"] - 1.0 / (3 + 1)) < 1e-6  # 0.25

    def test_equal_scores_stable_sort(self) -> None:
        """Equal RRF scores preserve input order (stable sort)."""
        ids = [f"doc-{i}" for i in range(20)]
        v_table = _make_vector_table(ids)
        f_table = _make_fts_table(ids)

        result = HybridSearchBridge._rrf_fuse(v_table, f_table, k=60, top_k=20)
        result_ids = result.column("id").to_pylist()
        assert result_ids == ids
