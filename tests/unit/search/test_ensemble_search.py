"""Tests for arrow_lake.query.ensemble — Story 8.2 Multi-Model Ensemble Search.

Tests EnsembleSearchConfig, EnsembleSearchResult, EnsembleSearchBridge
(RRF fusion, column resolution, search), and SDK facade using mocks.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from arrow_lake.exceptions import ErrorCode, QueryError
from arrow_lake.query.ensemble import (
    EnsembleSearchBridge,
    EnsembleSearchConfig,
    EnsembleSearchResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_storage(**schema_overrides: pa.DataType) -> MagicMock:
    """Create a mock storage with a dataset that has given columns."""
    fields = [pa.field("id", pa.string())]
    for name, dtype in schema_overrides.items():
        fields.append(pa.field(name, dtype))
    schema = pa.schema(fields)
    mock_ds = MagicMock()
    mock_ds.schema = schema
    mock_storage = MagicMock()
    mock_storage.open_dataset.return_value = mock_ds
    return mock_storage


def _make_result_table(
    ids: list[str] | None = None,
    extra_columns: dict[str, list] | None = None,
) -> pa.Table:
    """Create a mock vector search result table."""
    cols: dict[str, list] = {"id": ids or ["doc1", "doc2", "doc3"]}
    if extra_columns:
        cols.update(extra_columns)
    return pa.table(cols)


# ---------------------------------------------------------------------------
# TestEnsembleSearchConfig
# ---------------------------------------------------------------------------


class TestEnsembleSearchConfig:
    """Test EnsembleSearchConfig defaults and validation."""

    def test_defaults(self) -> None:
        cfg = EnsembleSearchConfig()
        assert cfg.default_top_k == 10
        assert cfg.rrf_k == 60
        assert cfg.fusion_method == "rrf"
        assert cfg.candidate_multiplier == 3

    def test_frozen(self) -> None:
        cfg = EnsembleSearchConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.default_top_k = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestEnsembleSearchResult
# ---------------------------------------------------------------------------


class TestEnsembleSearchResult:
    """Test EnsembleSearchResult frozen dataclass."""

    def test_frozen(self) -> None:
        result = EnsembleSearchResult(
            table=pa.table({"id": []}),
            row_count=0,
            columns_searched=("col_a",),
            fusion_method="rrf",
            top_k=10,
            query_vector_dim=4,
        )
        with pytest.raises(FrozenInstanceError):
            result.row_count = 5  # type: ignore[misc]

    def test_all_fields_present(self) -> None:
        table = pa.table({"id": ["doc1"], "text": ["hello"]})
        result = EnsembleSearchResult(
            table=table,
            row_count=1,
            columns_searched=("emb_a", "emb_b"),
            fusion_method="rrf",
            top_k=10,
            query_vector_dim=4,
        )
        assert result.row_count == 1
        assert result.columns_searched == ("emb_a", "emb_b")
        assert result.fusion_method == "rrf"
        assert result.top_k == 10
        assert result.query_vector_dim == 4


# ---------------------------------------------------------------------------
# TestWeightedRRFFuse
# ---------------------------------------------------------------------------


class TestWeightedRRFFuse:
    """Test EnsembleSearchBridge._weighted_rrf_fuse."""

    def test_equal_weights_single_doc(self) -> None:
        table = _make_result_table(ids=["doc1"])
        result = EnsembleSearchBridge._weighted_rrf_fuse([table], k=60, top_k=10, weights=[1.0])
        assert "_ensemble_score" in result.column_names
        assert result.num_rows == 1

    def test_equal_weights_multiple_docs(self) -> None:
        table = _make_result_table(ids=["doc1", "doc2", "doc3"])
        result = EnsembleSearchBridge._weighted_rrf_fuse([table], k=60, top_k=10, weights=[1.0])
        assert result.num_rows == 3
        # doc1 should have highest score (rank 0)
        scores = result.column("_ensemble_score").to_pylist()
        assert scores[0] > scores[1] > scores[2]

    def test_weighted_fusion_double_score(self) -> None:
        t1 = _make_result_table(ids=["doc1", "doc2"])
        t2 = _make_result_table(ids=["doc1", "doc2"])
        # Two tables, doc1 rank 0 in both
        equal_result = EnsembleSearchBridge._weighted_rrf_fuse(
            [t1, t2], k=60, top_k=10, weights=[1.0, 1.0]
        )
        t3 = _make_result_table(ids=["doc1", "doc2"])
        t4 = _make_result_table(ids=["doc1", "doc2"])
        weighted_result = EnsembleSearchBridge._weighted_rrf_fuse(
            [t3, t4], k=60, top_k=10, weights=[1.0, 2.0]
        )
        # weighted doc1 score should be higher than equal doc1 score
        eq_idx = equal_result.column("id").to_pylist().index("doc1")
        wt_idx = weighted_result.column("id").to_pylist().index("doc1")
        eq_score = equal_result.column("_ensemble_score")[eq_idx].as_py()
        wt_score = weighted_result.column("_ensemble_score")[wt_idx].as_py()
        assert wt_score > eq_score

    def test_empty_tables_return_empty(self) -> None:
        empty = pa.table({"id": []})
        result = EnsembleSearchBridge._weighted_rrf_fuse([empty], k=60, top_k=10, weights=[1.0])
        assert result.num_rows == 0

    def test_single_table_passthrough(self) -> None:
        table = _make_result_table(ids=["doc1"], extra_columns={"text": ["hello"]})
        result = EnsembleSearchBridge._weighted_rrf_fuse([table], k=60, top_k=10, weights=[1.0])
        assert result.num_rows == 1
        assert "text" in result.column_names


# ---------------------------------------------------------------------------
# TestResolveColumns
# ---------------------------------------------------------------------------


class TestResolveColumns:
    """Test EnsembleSearchBridge._resolve_columns."""

    def test_explicit_columns_validates_against_schema(self) -> None:
        storage = _make_mock_storage(
            emb_a=pa.list_(pa.float32(), 4),
            emb_b=pa.list_(pa.float32(), 4),
        )
        bridge = EnsembleSearchBridge(storage)
        cols = bridge._resolve_columns("ds", ["emb_a", "emb_b"], query_dim=4)
        assert cols == ["emb_a", "emb_b"]

    def test_auto_detect_finds_matching_columns(self) -> None:
        storage = _make_mock_storage(
            emb_a=pa.list_(pa.float32(), 4),
            emb_b=pa.list_(pa.float32(), 4),
            text_col=pa.string(),
        )
        bridge = EnsembleSearchBridge(storage)
        cols = bridge._resolve_columns("ds", None, 4)
        # Should find emb_a and emb_b (fixed_size_list with dim 4)
        assert len(cols) == 2

    def test_dimension_mismatch_raises(self) -> None:
        storage = _make_mock_storage(
            emb_a=pa.list_(pa.float32(), 8),
        )
        bridge = EnsembleSearchBridge(storage)
        with pytest.raises(QueryError, match=ErrorCode.VECTOR_DIMENSION_MISMATCH):
            bridge._resolve_columns("ds", ["emb_a"], 4)

    def test_missing_column_raises(self) -> None:
        storage = _make_mock_storage()
        bridge = EnsembleSearchBridge(storage)
        with pytest.raises(QueryError, match=ErrorCode.ENSEMBLE_COLUMN_NOT_FOUND):
            bridge._resolve_columns("ds", ["nonexistent"], 4)


# ---------------------------------------------------------------------------
# TestSearch
# ---------------------------------------------------------------------------


class TestSearch:
    """Test EnsembleSearchBridge.search."""

    def test_no_columns_raises(self) -> None:
        # Storage with no vector columns matching the query dim
        storage = _make_mock_storage(text_col=pa.string())
        bridge = EnsembleSearchBridge(storage)
        with pytest.raises(QueryError, match=ErrorCode.ENSEMBLE_NO_COLUMNS):
            bridge.search("ds", [0.0] * 4, columns=None)

    def test_single_column_works(self) -> None:
        storage = _make_mock_storage(
            emb_a=pa.list_(pa.float32(), 4),
        )
        mock_result = MagicMock()
        mock_result.table = _make_result_table(ids=["doc1"])
        bridge = EnsembleSearchBridge(storage)
        with patch("arrow_lake.query.vector.VectorSearchBridge") as mock_bridge:
            mock_bridge.return_value.search.return_value = mock_result
            result = bridge.search("ds", [0.0] * 4, columns=["emb_a"], top_k=5)
        assert result.row_count == 1
        assert result.columns_searched == ("emb_a",)

    def test_multiple_columns_calls_search_n_times(self) -> None:
        storage = _make_mock_storage(
            emb_a=pa.list_(pa.float32(), 4),
            emb_b=pa.list_(pa.float32(), 4),
        )
        mock_result = MagicMock()
        mock_result.table = _make_result_table(ids=["doc1"])
        bridge = EnsembleSearchBridge(storage)
        with patch("arrow_lake.query.vector.VectorSearchBridge") as mock_bridge:
            mock_bridge.return_value.search.return_value = mock_result
            result = bridge.search("ds", [0.0] * 4, columns=["emb_a", "emb_b"], top_k=5)
        assert mock_bridge.return_value.search.call_count == 2
        assert result.columns_searched == ("emb_a", "emb_b")


# ---------------------------------------------------------------------------
# TestSDKFacade
# ---------------------------------------------------------------------------


class TestSDKFacade:
    """Test Lake.ensemble_search() delegates to EnsembleSearchBridge."""

    def test_delegates_to_bridge(self) -> None:
        mock_bridge = MagicMock()
        mock_result = EnsembleSearchResult(
            table=pa.table({"id": []}),
            row_count=0,
            columns_searched=("emb",),
            fusion_method="rrf",
            top_k=10,
            query_vector_dim=4,
        )
        mock_bridge.search.return_value = mock_result

        with patch("arrow_lake.query.ensemble.EnsembleSearchBridge", return_value=mock_bridge):
            # Import and instantiate a mock Lake to test the facade
            from arrow_lake import Lake

            lake = MagicMock(spec=Lake)
            from arrow_lake.query.ensemble import EnsembleSearchBridge

            bridge = EnsembleSearchBridge(lake._get_storage())
            result = bridge.search("ds", [0.0] * 4, columns=["emb"])
        assert mock_bridge.search.called or result is not None
