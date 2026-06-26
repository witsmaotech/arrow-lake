"""Tests for arrow_lake.query.hybrid — Story 5.3.

Tests HybridSearchBridge:
- DTO frozen dataclass
- _rrf_fuse (basic, empty, partial overlap, top_k, K value)
- search (validation, delegation, where clause)
- Config-driven defaults
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

pytest.importorskip("lancedb")

from arrow_lake.exceptions import QueryError
from arrow_lake.query.hybrid import HybridSearchBridge, HybridSearchResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_storage() -> MagicMock:
    """Create a mock LanceStorageManager (no dataset_uri → sub-bridge path)."""
    storage = MagicMock()
    del storage.dataset_uri
    return storage


def _make_vector_result_table(
    ids: list[str],
    distances: list[float] | None = None,
) -> pa.Table:
    """Create a mock vector search result table."""
    if distances is None:
        distances = [0.1 * (i + 1) for i in range(len(ids))]
    return pa.table(
        {
            "id": ids,
            "modality": ["text"] * len(ids),
            "source": ["src-0"] * len(ids),
            "_distance": distances,
        }
    )


def _make_fts_result_table(
    ids: list[str],
    scores: list[float] | None = None,
) -> pa.Table:
    """Create a mock FTS search result table."""
    if scores is None:
        scores = [2.0 - 0.2 * i for i in range(len(ids))]
    return pa.table(
        {
            "id": ids,
            "modality": ["text"] * len(ids),
            "source": ["src-0"] * len(ids),
            "_score": scores,
        }
    )


# ---------------------------------------------------------------------------
# DTO Tests
# ---------------------------------------------------------------------------


class TestHybridSearchResult:
    """Test HybridSearchResult frozen dataclass."""

    def test_is_frozen(self) -> None:
        table = pa.table({"_rrf_score": [0.03, 0.02]})
        result = HybridSearchResult(
            table=table,
            row_count=2,
            query_text="test query",
            query_vector_dim=384,
            top_k=10,
            rrf_k=60,
            max_rrf_score=0.03,
        )
        with pytest.raises(FrozenInstanceError):
            result.row_count = 5  # type: ignore[misc]

    def test_all_fields_accessible(self) -> None:
        table = pa.table({"_rrf_score": [0.05]})
        result = HybridSearchResult(
            table=table,
            row_count=1,
            query_text="hello",
            query_vector_dim=384,
            top_k=5,
            rrf_k=60,
            max_rrf_score=0.05,
        )
        assert result.row_count == 1
        assert result.query_text == "hello"
        assert result.query_vector_dim == 384
        assert result.top_k == 5
        assert result.rrf_k == 60
        assert result.max_rrf_score == 0.05

    def test_max_rrf_score_none_for_empty(self) -> None:
        table = pa.table({"_rrf_score": []})
        result = HybridSearchResult(
            table=table,
            row_count=0,
            query_text="nothing",
            query_vector_dim=384,
            top_k=10,
            rrf_k=60,
            max_rrf_score=None,
        )
        assert result.max_rrf_score is None


# ---------------------------------------------------------------------------
# _rrf_fuse Tests
# ---------------------------------------------------------------------------


class TestRRFFuse:
    """Test HybridSearchBridge._rrf_fuse static method."""

    def test_basic_fusion(self) -> None:
        """Vector + FTS results are fused with RRF scoring."""
        v_table = _make_vector_result_table(["doc-1", "doc-2", "doc-3"])
        f_table = _make_fts_result_table(["doc-2", "doc-3", "doc-4"])

        result = HybridSearchBridge._rrf_fuse(v_table, f_table, k=60, top_k=10)

        assert result.num_rows > 0
        assert "_rrf_score" in result.column_names
        # doc-2 and doc-3 appear in both lists — should have higher scores
        scores = {row["id"]: row["_rrf_score"] for row in result.to_pylist()}
        assert "doc-1" in scores
        assert "doc-2" in scores
        assert "doc-3" in scores
        assert "doc-4" in scores
        # doc-2 appears rank 0 in FTS and rank 1 in vector → highest RRF
        assert scores["doc-2"] > scores["doc-1"]
        assert scores["doc-3"] > scores["doc-1"]

    def test_only_vector_results(self) -> None:
        """FTS returns empty — only vector results returned."""
        v_table = _make_vector_result_table(["doc-1", "doc-2"])
        f_table = _make_fts_result_table([])

        result = HybridSearchBridge._rrf_fuse(v_table, f_table, k=60, top_k=10)

        assert result.num_rows == 2
        ids = result.column("id").to_pylist()
        assert "doc-1" in ids
        assert "doc-2" in ids

    def test_only_fts_results(self) -> None:
        """Vector returns empty — only FTS results returned."""
        v_table = _make_vector_result_table([])
        f_table = _make_fts_result_table(["doc-1", "doc-2"])

        result = HybridSearchBridge._rrf_fuse(v_table, f_table, k=60, top_k=10)

        assert result.num_rows == 2
        ids = result.column("id").to_pylist()
        assert "doc-1" in ids
        assert "doc-2" in ids

    def test_no_overlap(self) -> None:
        """Vector and FTS have no common ids."""
        v_table = _make_vector_result_table(["doc-1", "doc-2", "doc-3"])
        f_table = _make_fts_result_table(["doc-4", "doc-5", "doc-6"])

        result = HybridSearchBridge._rrf_fuse(v_table, f_table, k=60, top_k=10)

        assert result.num_rows == 6

    def test_full_overlap(self) -> None:
        """Same ids in both — RRF boosts them."""
        v_table = _make_vector_result_table(["doc-1", "doc-2", "doc-3"])
        f_table = _make_fts_result_table(["doc-1", "doc-2", "doc-3"])

        result = HybridSearchBridge._rrf_fuse(v_table, f_table, k=60, top_k=10)

        assert result.num_rows == 3
        scores = result.column("_rrf_score").to_pylist()
        # All docs appear in both → all have 2x score
        for s in scores:
            assert s > 0.02  # 1/(1+60) ≈ 0.0163, doubled ≈ 0.0326

    def test_top_k_truncation(self) -> None:
        """Fused result is truncated to top_k."""
        v_table = _make_vector_result_table([f"doc-{i}" for i in range(10)])
        f_table = _make_fts_result_table([f"doc-{i}" for i in range(10)])

        result = HybridSearchBridge._rrf_fuse(v_table, f_table, k=60, top_k=3)

        assert result.num_rows == 3

    def test_rrf_k_value_affects_scores(self) -> None:
        """Different K values produce different scores."""
        v_table = _make_vector_result_table(["doc-1"])
        f_table = _make_fts_result_table(["doc-1"])

        result_k60 = HybridSearchBridge._rrf_fuse(v_table, f_table, k=60, top_k=10)
        result_k10 = HybridSearchBridge._rrf_fuse(v_table, f_table, k=10, top_k=10)

        score_k60 = result_k60.column("_rrf_score")[0].as_py()
        score_k10 = result_k10.column("_rrf_score")[0].as_py()

        # Lower K → higher scores (1/(1+10) > 1/(1+60))
        assert score_k10 > score_k60

    def test_both_empty(self) -> None:
        """Both tables empty → empty result."""
        v_table = _make_vector_result_table([])
        f_table = _make_fts_result_table([])

        result = HybridSearchBridge._rrf_fuse(v_table, f_table, k=60, top_k=10)

        assert result.num_rows == 0
        assert "_rrf_score" in result.column_names

    def test_results_sorted_by_score_descending(self) -> None:
        """Results are sorted by RRF score descending."""
        v_table = _make_vector_result_table(["doc-1", "doc-2", "doc-3", "doc-4"])
        # doc-1 at rank 0 in both → highest score
        f_table = _make_fts_result_table(["doc-1", "doc-5", "doc-6", "doc-7"])

        result = HybridSearchBridge._rrf_fuse(v_table, f_table, k=60, top_k=10)

        scores = result.column("_rrf_score").to_pylist()
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# HybridSearchBridge.search Tests
# ---------------------------------------------------------------------------


class TestSearch:
    """Test HybridSearchBridge.search."""

    def test_search_success(self) -> None:
        """Happy path: delegates to vector + FTS bridges."""
        from arrow_lake.query.fts import FullTextSearchResult
        from arrow_lake.query.vector import VectorSearchResult

        storage = _make_mock_storage()
        bridge = HybridSearchBridge(storage)

        vector_result = _make_vector_result_table(["doc-1", "doc-2"])
        fts_result = _make_fts_result_table(["doc-2", "doc-3"])

        mock_vector_bridge = MagicMock()
        mock_fts_bridge = MagicMock()

        mock_vector_bridge.search.return_value = VectorSearchResult(
            table=vector_result,
            row_count=2,
            query_vector_dim=384,
            metric="cosine",
            top_k=30,
            max_distance=0.2,
        )
        mock_fts_bridge.search.return_value = FullTextSearchResult(
            table=fts_result,
            row_count=2,
            query="machine learning",
            top_k=30,
            fts_column="text_content",
            max_score=2.0,
        )

        # Patch at source modules (lazy import resolution)
        with (
            patch("arrow_lake.query.vector.VectorSearchBridge", return_value=mock_vector_bridge),
            patch("arrow_lake.query.fts.FullTextSearchBridge", return_value=mock_fts_bridge),
        ):
            result = bridge.search(
                "test_ds",
                [0.0] * 384,
                "machine learning",
                top_k=10,
            )

        assert isinstance(result, HybridSearchResult)
        assert result.row_count > 0
        assert result.query_text == "machine learning"
        assert result.query_vector_dim == 384
        assert result.top_k == 10
        assert result.rrf_k == 60

    def test_empty_query_vector_raises(self) -> None:
        """Raise HYBRID_SEARCH_FAILED when query vector is empty."""
        storage = _make_mock_storage()
        bridge = HybridSearchBridge(storage)

        with pytest.raises(QueryError, match="vector must not be empty"):
            bridge.search("test_ds", [], "hello")

    def test_empty_query_text_raises(self) -> None:
        """Raise HYBRID_SEARCH_FAILED when query text is empty."""
        storage = _make_mock_storage()
        bridge = HybridSearchBridge(storage)

        with pytest.raises(QueryError, match="text must not be empty"):
            bridge.search("test_ds", [0.0] * 384, "")

        with pytest.raises(QueryError, match="text must not be empty"):
            bridge.search("test_ds", [0.0] * 384, "   ")

        with pytest.raises(QueryError, match="text must not be empty"):
            bridge.search("test_ds", [0.0] * 384, "\t\n")

    def test_invalid_top_k_raises(self) -> None:
        """Raise HYBRID_SEARCH_FAILED when top_k < 1."""
        storage = _make_mock_storage()
        bridge = HybridSearchBridge(storage)

        with pytest.raises(QueryError, match="top_k must be >= 1"):
            bridge.search("test_ds", [0.0] * 384, "hello", top_k=0)

    def test_where_clause_validated(self) -> None:
        """Where clause with dangerous keywords raises."""
        storage = _make_mock_storage()
        bridge = HybridSearchBridge(storage)

        with pytest.raises(QueryError, match="dangerous"):
            bridge.search("test_ds", [0.0] * 384, "hello", where="DROP TABLE")


# ---------------------------------------------------------------------------
# Config-driven defaults
# ---------------------------------------------------------------------------


class TestConfigDrivenDefaults:
    """Test HybridSearchConfig drives bridge defaults."""

    def test_config_drives_rrf_k(self) -> None:
        """Config rrf_k is used in result."""
        from arrow_lake.config import HybridSearchConfig

        storage = _make_mock_storage()
        config = HybridSearchConfig(rrf_k=100)
        bridge = HybridSearchBridge(storage, config=config)

        assert bridge._config.rrf_k == 100

    def test_config_drives_top_k_multiplier(self) -> None:
        """Config multipliers drive candidate pool sizes."""
        from arrow_lake.config import HybridSearchConfig

        storage = _make_mock_storage()
        config = HybridSearchConfig(
            default_top_k=5,
            vector_top_k_multiplier=4,
            fts_top_k_multiplier=2,
        )
        bridge = HybridSearchBridge(storage, config=config)

        assert bridge._config.default_top_k == 5
        assert bridge._config.vector_top_k_multiplier == 4
        assert bridge._config.fts_top_k_multiplier == 2

    def test_no_config_uses_defaults(self) -> None:
        """Bridge without config uses HybridSearchConfig defaults."""

        storage = _make_mock_storage()
        bridge = HybridSearchBridge(storage)

        assert bridge._config.default_top_k == 10
        assert bridge._config.rrf_k == 60
        assert bridge._config.vector_top_k_multiplier == 3
        assert bridge._config.fts_top_k_multiplier == 3


class TestRerankTable:
    """HybridSearchBridge._rerank_table (v1.8.0 #5 cross-encoder 精排)。"""

    def test_noop_preserves_order(self) -> None:
        from arrow_lake.config import HybridSearchConfig

        bridge = HybridSearchBridge(
            storage=_make_mock_storage(),
            config=HybridSearchConfig(reranker_type="none"),
        )
        table = pa.table({"text_content": ["a", "b"], "_rrf_score": [0.5, 0.3]})
        result = bridge._rerank_table(table, "q", "text_content", 2)
        # none → NoopReranker，原序 + _rerank_score 列
        assert result.num_rows == 2
        assert "_rerank_score" in result.column_names
        assert result.column("text_content").to_pylist() == ["a", "b"]

    def test_cross_encoder_reorders(self) -> None:
        from arrow_lake.config import HybridSearchConfig

        bridge = HybridSearchBridge(
            storage=_make_mock_storage(),
            config=HybridSearchConfig(reranker_type="cross-encoder"),
        )
        mock_reranker = MagicMock()

        def fake_rerank(query, chunks, top_n):
            return list(reversed(chunks))[:top_n]

        mock_reranker.rerank.side_effect = fake_rerank
        bridge._reranker = mock_reranker  # 注入，跳过 create_reranker

        table = pa.table({"text_content": ["a", "b"], "_rrf_score": [0.5, 0.3]})
        result = bridge._rerank_table(table, "query", "text_content", 2)

        assert "_rerank_score" in result.column_names
        assert result.column("text_content").to_pylist() == ["b", "a"]

    def test_missing_text_column_returns_unchanged(self) -> None:
        from arrow_lake.config import HybridSearchConfig

        bridge = HybridSearchBridge(
            storage=_make_mock_storage(),
            config=HybridSearchConfig(reranker_type="cross-encoder"),
        )
        table = pa.table({"other": ["a"], "_rrf_score": [0.5]})
        result = bridge._rerank_table(table, "q", "text_content", 1)
        assert result is table

    def test_rerank_failure_returns_original(self) -> None:
        from arrow_lake.config import HybridSearchConfig

        bridge = HybridSearchBridge(
            storage=_make_mock_storage(),
            config=HybridSearchConfig(reranker_type="cross-encoder"),
        )
        mock_reranker = MagicMock()
        mock_reranker.rerank.side_effect = RuntimeError("boom")
        bridge._reranker = mock_reranker
        table = pa.table({"text_content": ["a", "b"], "_rrf_score": [0.5, 0.3]})
        result = bridge._rerank_table(table, "q", "text_content", 2)
        assert result is table
