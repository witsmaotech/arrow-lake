"""Tests for arrow_lake.query.vector — Story 5.1.

Tests VectorSearchBridge:
- DTO frozen dataclasses
- create_index (IVF_PQ, IVF_HNSW_PQ, replace)
- search (top_k, _distance, where filter, empty results)
- get_index_info (with/without index)
- Edge cases (dimension mismatch, too few rows)
- Auto partition selection
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from arrow_lake.exceptions import QueryError
from arrow_lake.query.vector import (
    IndexInfo,
    VectorSearchBridge,
    VectorSearchResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_storage() -> MagicMock:
    """Create a mock LanceStorageManager."""
    return MagicMock()


def _make_mock_lance_table(
    *,
    row_count: int = 1000,
    has_vector_column: bool = True,
    vector_dim: int = 384,
    has_index: bool = False,
    index_name: str = "text_embedding_idx",
) -> MagicMock:
    """Create a mock LanceDB table with configurable properties."""
    table = MagicMock()

    # count_rows
    table.count_rows.return_value = row_count

    # schema with vector column
    if has_vector_column:
        vector_type = MagicMock()
        vector_type.list_size = vector_dim
        vector_field = MagicMock()
        vector_field.name = "text_embedding"
        vector_field.type = vector_type
        schema = MagicMock()
        schema.names = ["text_embedding", "modality", "source"]
        schema.field.side_effect = lambda name: {
            "text_embedding": vector_field,
        }.get(name, MagicMock())
        table.schema = schema
    else:
        table.schema = MagicMock(names=["modality", "source"])

    # list_indices
    if has_index:
        idx_config = MagicMock()
        idx_config.name = index_name
        idx_config.columns = ["text_embedding"]
        idx_config.index_type = "IVF_PQ"
        table.list_indices.return_value = [idx_config]

        stats = MagicMock()
        stats.index_type = "IVF_PQ"
        stats.distance_type = "cosine"
        stats.num_indexed_rows = row_count
        stats.num_unindexed_rows = 0
        table.index_stats.return_value = stats
    else:
        table.list_indices.return_value = []

    return table


def _make_mock_query_builder(result_table: pa.Table | None = None) -> MagicMock:
    """Create a mock LanceDB query builder with fluent API."""
    builder = MagicMock()

    if result_table is None:
        # Default empty table with _distance column
        result_table = pa.table(
            {
                "text_embedding": [],
                "modality": [],
                "source": [],
                "_distance": [],
            }
        )

    builder.to_arrow.return_value = result_table
    # Fluent API: each method returns self
    builder.where.return_value = builder
    builder.limit.return_value = builder
    builder.nprobes.return_value = builder
    builder.distance_type.return_value = builder
    return builder


# ---------------------------------------------------------------------------
# DTO Tests
# ---------------------------------------------------------------------------


class TestVectorSearchResult:
    """Test VectorSearchResult frozen dataclass."""

    def test_is_frozen(self) -> None:
        table = pa.table({"_distance": [0.1, 0.2]})
        result = VectorSearchResult(
            table=table,
            row_count=2,
            query_vector_dim=384,
            metric="cosine",
            top_k=10,
            max_distance=0.2,
        )
        with pytest.raises(FrozenInstanceError):
            result.row_count = 5  # type: ignore[misc]

    def test_all_fields_accessible(self) -> None:
        table = pa.table({"_distance": [0.1]})
        result = VectorSearchResult(
            table=table,
            row_count=1,
            query_vector_dim=384,
            metric="cosine",
            top_k=5,
            max_distance=0.1,
        )
        assert result.row_count == 1
        assert result.query_vector_dim == 384
        assert result.metric == "cosine"
        assert result.top_k == 5
        assert result.max_distance == 0.1

    def test_max_distance_none_for_empty(self) -> None:
        table = pa.table({"_distance": []})
        result = VectorSearchResult(
            table=table,
            row_count=0,
            query_vector_dim=384,
            metric="cosine",
            top_k=10,
            max_distance=None,
        )
        assert result.max_distance is None


class TestIndexInfo:
    """Test IndexInfo frozen dataclass."""

    def test_is_frozen(self) -> None:
        info = IndexInfo(
            name="idx",
            index_type="IVF_PQ",
            distance_type="cosine",
            num_indexed_rows=100,
            num_unindexed_rows=0,
            columns=["text_embedding"],
        )
        with pytest.raises(FrozenInstanceError):
            info.num_indexed_rows = 200  # type: ignore[misc]

    def test_all_fields_accessible(self) -> None:
        info = IndexInfo(
            name="text_embedding_idx",
            index_type="IVF_PQ",
            distance_type="cosine",
            num_indexed_rows=1000,
            num_unindexed_rows=0,
            columns=["text_embedding"],
        )
        assert info.name == "text_embedding_idx"
        assert info.index_type == "IVF_PQ"
        assert info.distance_type == "cosine"
        assert info.num_indexed_rows == 1000
        assert info.columns == ["text_embedding"]


# ---------------------------------------------------------------------------
# VectorSearchBridge.create_index Tests
# ---------------------------------------------------------------------------


class TestCreateIndex:
    """Test VectorSearchBridge.create_index."""

    def test_create_ivf_pq_index(self) -> None:
        """Happy path: create IVF_PQ index with sufficient rows."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(row_count=1000, has_index=True)
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        result = bridge.create_index("test_ds", metric="cosine")

        assert isinstance(result, IndexInfo)
        mock_table.create_index.assert_called_once()
        call_kwargs = mock_table.create_index.call_args[1]
        assert call_kwargs["metric"] == "cosine"
        assert call_kwargs["index_type"] == "IVF_PQ"
        assert call_kwargs["vector_column_name"] == "text_embedding"

    def test_create_index_ivf_hnsw_pq(self) -> None:
        """Create IVF_HNSW_PQ index."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(row_count=1000, has_index=True)
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        result = bridge.create_index("test_ds", index_type="IVF_HNSW_PQ")

        assert isinstance(result, IndexInfo)
        call_kwargs = mock_table.create_index.call_args[1]
        assert call_kwargs["index_type"] == "IVF_HNSW_PQ"

    def test_create_index_replace(self) -> None:
        """replace=True replaces existing index."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(row_count=1000, has_index=True)
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        bridge.create_index("test_ds", replace=True)

        call_kwargs = mock_table.create_index.call_args[1]
        assert call_kwargs["replace"] is True

    def test_create_index_too_few_rows(self) -> None:
        """Raise VECTOR_INDEX_TOO_FEW_ROWS when <256 rows."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(row_count=100)
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        with pytest.raises(QueryError, match="TOO_FEW_ROWS"):
            bridge.create_index("test_ds")

    def test_create_index_column_not_found(self) -> None:
        """Raise VECTOR_SEARCH_FAILED when vector column missing."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(has_vector_column=False, row_count=500)
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        with pytest.raises(QueryError, match="not found"):
            bridge.create_index("test_ds", vector_column="nonexistent")

    def test_create_index_failure(self) -> None:
        """Raise VECTOR_INDEX_FAILED on create_index exception."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(row_count=500)
        mock_table.create_index.side_effect = RuntimeError("Lance error")
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        with pytest.raises(QueryError, match="Failed to create vector index"):
            bridge.create_index("test_ds")

    def test_create_index_auto_partitions_small(self) -> None:
        """Small dataset (<1M) uses default 256 partitions."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(row_count=10000, has_index=True)
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        bridge.create_index("test_ds")

        call_kwargs = mock_table.create_index.call_args[1]
        assert call_kwargs["num_partitions"] == 256

    def test_create_index_auto_partitions_large(self) -> None:
        """Large dataset (>=1M) uses sqrt(n) partitions."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(row_count=2_000_000, has_index=True)
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        bridge.create_index("test_ds")

        call_kwargs = mock_table.create_index.call_args[1]
        # sqrt(2000000) ~ 1414, should be used
        assert call_kwargs["num_partitions"] == int((2_000_000) ** 0.5)


# ---------------------------------------------------------------------------
# VectorSearchBridge.search Tests
# ---------------------------------------------------------------------------


class TestSearch:
    """Test VectorSearchBridge.search."""

    def test_search_returns_results(self) -> None:
        """Happy path: search returns VectorSearchResult."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table()
        storage.open_dataset.return_value = mock_table

        result_table = pa.table(
            {
                "text_embedding": [[0.0] * 384, [0.0] * 384],
                "modality": ["text", "text"],
                "_distance": [0.1, 0.2],
            }
        )
        builder = _make_mock_query_builder(result_table)
        mock_table.search.return_value = builder

        bridge = VectorSearchBridge(storage)
        query_vector = [0.0] * 384
        result = bridge.search("test_ds", query_vector, top_k=10)

        assert isinstance(result, VectorSearchResult)
        assert result.row_count == 2
        assert result.max_distance == 0.2
        assert result.query_vector_dim == 384
        assert result.top_k == 10
        assert result.metric == "cosine"

    def test_search_distance_column_present(self) -> None:
        """Result table includes _distance column."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table()
        storage.open_dataset.return_value = mock_table

        result_table = pa.table(
            {
                "modality": ["text"],
                "_distance": [0.15],
            }
        )
        builder = _make_mock_query_builder(result_table)
        mock_table.search.return_value = builder

        bridge = VectorSearchBridge(storage)
        result = bridge.search("test_ds", [0.0] * 384)

        assert "_distance" in result.table.column_names

    def test_search_with_where_filter(self) -> None:
        """Where clause is passed to the query builder."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table()
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_query_builder()
        mock_table.search.return_value = builder

        bridge = VectorSearchBridge(storage)
        bridge.search("test_ds", [0.0] * 384, where="modality = 'text'")

        builder.where.assert_called_once_with("modality = 'text'")

    def test_search_with_custom_metric(self) -> None:
        """Custom metric is passed to the query builder."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table()
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_query_builder()
        mock_table.search.return_value = builder

        bridge = VectorSearchBridge(storage)
        bridge.search("test_ds", [0.0] * 384, metric="l2")

        builder.distance_type.assert_called_once_with("l2")

    def test_search_with_nprobes(self) -> None:
        """nprobes is passed to the query builder."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table()
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_query_builder()
        mock_table.search.return_value = builder

        bridge = VectorSearchBridge(storage)
        bridge.search("test_ds", [0.0] * 384, nprobes=50)

        builder.nprobes.assert_called_once_with(50)

    def test_search_empty_results(self) -> None:
        """Empty results return 0-row table with max_distance=None."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table()
        storage.open_dataset.return_value = mock_table

        result_table = pa.table(
            {
                "text_embedding": [],
                "modality": [],
                "_distance": [],
            }
        )
        builder = _make_mock_query_builder(result_table)
        mock_table.search.return_value = builder

        bridge = VectorSearchBridge(storage)
        result = bridge.search("test_ds", [0.0] * 384)

        assert result.row_count == 0
        assert result.max_distance is None
        assert result.table.num_rows == 0

    def test_search_dimension_mismatch(self) -> None:
        """Raise VECTOR_DIMENSION_MISMATCH on wrong vector size."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(vector_dim=384)
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        wrong_vector = [0.0] * 128  # Wrong dimension

        with pytest.raises(QueryError, match="DIMENSION_MISMATCH"):
            bridge.search("test_ds", wrong_vector)

    def test_search_column_not_found(self) -> None:
        """Raise VECTOR_SEARCH_FAILED when vector column missing."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(has_vector_column=False)
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        with pytest.raises(QueryError, match="not found"):
            bridge.search("test_ds", [0.0] * 384, vector_column="nonexistent")

    def test_search_failure(self) -> None:
        """Raise VECTOR_SEARCH_FAILED on search exception."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table()
        storage.open_dataset.return_value = mock_table

        # Mock search() to succeed but to_arrow() to fail
        builder = MagicMock()
        builder.to_arrow.side_effect = RuntimeError("Lance error")
        builder.where.return_value = builder
        builder.limit.return_value = builder
        builder.nprobes.return_value = builder
        builder.distance_type.return_value = builder
        mock_table.search.return_value = builder

        bridge = VectorSearchBridge(storage)
        with pytest.raises(QueryError, match="Vector search failed"):
            bridge.search("test_ds", [0.0] * 384)


# ---------------------------------------------------------------------------
# VectorSearchBridge.get_index_info Tests
# ---------------------------------------------------------------------------


class TestGetIndexInfo:
    """Test VectorSearchBridge.get_index_info."""

    def test_returns_info_when_index_exists(self) -> None:
        """Return IndexInfo when vector index exists."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(has_index=True)
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        result = bridge.get_index_info("test_ds")

        assert result is not None
        assert isinstance(result, IndexInfo)
        assert result.index_type == "IVF_PQ"
        assert result.distance_type == "cosine"

    def test_returns_none_when_no_index(self) -> None:
        """Return None when no vector index exists."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(has_index=False)
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        result = bridge.get_index_info("test_ds")

        assert result is None

    def test_returns_none_when_no_vector_column(self) -> None:
        """Return None when no vector columns in schema."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(has_vector_column=False, has_index=False)
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        result = bridge.get_index_info("test_ds")

        assert result is None


# ---------------------------------------------------------------------------
# Static Method Tests
# ---------------------------------------------------------------------------


class TestAutoPartitions:
    """Test VectorSearchBridge._auto_select_partitions."""

    def test_small_dataset_uses_base(self) -> None:
        assert VectorSearchBridge._auto_select_partitions(100_000) == 256

    def test_exactly_1m_uses_sqrt(self) -> None:
        result = VectorSearchBridge._auto_select_partitions(1_000_000)
        assert result == int((1_000_000) ** 0.5)

    def test_large_dataset_uses_sqrt(self) -> None:
        result = VectorSearchBridge._auto_select_partitions(4_000_000)
        expected = int((4_000_000) ** 0.5)  # 2000
        assert result == expected

    def test_very_large_dataset_capped(self) -> None:
        result = VectorSearchBridge._auto_select_partitions(100_000_000)
        assert result == 4096  # sqrt(100M) = 10000, capped at 4096

    def test_custom_base_partitions(self) -> None:
        result = VectorSearchBridge._auto_select_partitions(500_000, base_partitions=128)
        assert result == 128


class TestGetVectorDimension:
    """Test VectorSearchBridge._get_vector_dimension."""

    def test_fixed_size_list_returns_dimension(self) -> None:
        schema = pa.schema(
            [
                ("text_embedding", pa.list_(pa.float32(), 384)),
                ("modality", pa.string()),
            ]
        )
        dim = VectorSearchBridge._get_vector_dimension(schema, "text_embedding")
        assert dim == 384

    def test_plain_list_returns_zero(self) -> None:
        schema = pa.schema(
            [
                ("items", pa.list_(pa.float32())),
            ]
        )
        dim = VectorSearchBridge._get_vector_dimension(schema, "items")
        assert dim == 0

    def test_non_list_returns_zero(self) -> None:
        schema = pa.schema(
            [
                ("name", pa.string()),
            ]
        )
        dim = VectorSearchBridge._get_vector_dimension(schema, "name")
        assert dim == 0


# ---------------------------------------------------------------------------
# C1: Where clause injection prevention
# ---------------------------------------------------------------------------


class TestWhereClauseValidation:
    """Test _validate_where_clause injection prevention (C1)."""

    def test_safe_where_clause_passes(self) -> None:
        """Safe filter expressions should not raise."""
        VectorSearchBridge._validate_where_clause("modality = 'text'")
        VectorSearchBridge._validate_where_clause("source = 'src-0' AND modality = 'text'")
        VectorSearchBridge._validate_where_clause("id IN ('1', '2', '3')")

    def test_dangerous_keywords_blocked(self) -> None:
        """Dangerous SQL keywords should raise VECTOR_INVALID_QUERY."""
        for keyword in ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "TRUNCATE"]:
            with pytest.raises(QueryError, match="INVALID_QUERY"):
                VectorSearchBridge._validate_where_clause(f"{keyword} TABLE")

    def test_union_blocked(self) -> None:
        with pytest.raises(QueryError, match="INVALID_QUERY"):
            VectorSearchBridge._validate_where_clause("1=1 UNION SELECT *")

    def test_case_insensitive_detection(self) -> None:
        with pytest.raises(QueryError, match="INVALID_QUERY"):
            VectorSearchBridge._validate_where_clause("drop table users")


# ---------------------------------------------------------------------------
# M5: Empty query vector validation
# ---------------------------------------------------------------------------


class TestEmptyVectorValidation:
    """Test empty query vector raises VECTOR_INVALID_QUERY (M5)."""

    def test_empty_list_raises(self) -> None:
        storage = _make_mock_storage()
        bridge = VectorSearchBridge(storage)

        with pytest.raises(QueryError, match="INVALID_QUERY"):
            bridge.search("test_ds", [])


# ---------------------------------------------------------------------------
# M2: Variable-length list dimension
# ---------------------------------------------------------------------------


class TestVariableLengthListDimension:
    """Test dimension 0 raises error for variable-length list (M2)."""

    def test_variable_length_list_raises(self) -> None:
        """Variable-length list (dim=0) raises DIMENSION_MISMATCH."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table()
        # Override: use real pa.list_ (no list_size) instead of fixed_size_list
        var_list_type = pa.list_(pa.float32())
        var_list_field = MagicMock()
        var_list_field.type = var_list_type
        mock_table.schema.field.side_effect = lambda name: {
            "text_embedding": var_list_field,
        }.get(name, MagicMock())
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        with pytest.raises(QueryError, match="DIMENSION_MISMATCH"):
            bridge.search("test_ds", [0.0] * 384)


# ---------------------------------------------------------------------------
# M3: get_index_info with custom vector_column
# ---------------------------------------------------------------------------


class TestGetIndexInfoWithColumn:
    """Test get_index_info with explicit vector_column (M3)."""

    def test_custom_column_with_index(self) -> None:
        """Return index info when explicitly checking a column with an index."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(
            has_index=True,
            index_name="custom_idx",
        )
        idx_config = MagicMock()
        idx_config.name = "custom_idx"
        idx_config.columns = ["audio_embedding"]
        idx_config.index_type = "IVF_PQ"
        mock_table.list_indices.return_value = [idx_config]
        mock_table.schema.names = ["audio_embedding", "modality"]
        storage.open_dataset.return_value = mock_table

        bridge = VectorSearchBridge(storage)
        result = bridge.get_index_info("test_ds", vector_column="audio_embedding")

        assert result is not None
        assert result.columns == ["audio_embedding"]


# ---------------------------------------------------------------------------
# Config-driven defaults
# ---------------------------------------------------------------------------


class TestConfigDrivenDefaults:
    """Test VectorSearchConfig drives bridge defaults (Tech debt fix)."""

    def test_create_index_uses_config_metric(self) -> None:
        """create_index uses config metric when not specified."""
        from arrow_lake.config import VectorSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(row_count=1000, has_index=True)
        storage.open_dataset.return_value = mock_table

        config = VectorSearchConfig(metric="l2")
        bridge = VectorSearchBridge(storage, config=config)
        bridge.create_index("test_ds")

        call_kwargs = mock_table.create_index.call_args[1]
        assert call_kwargs["metric"] == "l2"

    def test_create_index_uses_config_index_type(self) -> None:
        """create_index uses config default_index_type when not specified."""
        from arrow_lake.config import VectorSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(row_count=1000, has_index=True)
        storage.open_dataset.return_value = mock_table

        config = VectorSearchConfig(default_index_type="IVF_FLAT")
        bridge = VectorSearchBridge(storage, config=config)
        bridge.create_index("test_ds")

        call_kwargs = mock_table.create_index.call_args[1]
        assert call_kwargs["index_type"] == "IVF_FLAT"

    def test_create_index_uses_config_num_sub_vectors(self) -> None:
        """create_index uses config num_sub_vectors when not specified."""
        from arrow_lake.config import VectorSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(row_count=1000, has_index=True)
        storage.open_dataset.return_value = mock_table

        config = VectorSearchConfig(num_sub_vectors=16)
        bridge = VectorSearchBridge(storage, config=config)
        bridge.create_index("test_ds")

        call_kwargs = mock_table.create_index.call_args[1]
        assert call_kwargs["num_sub_vectors"] == 16

    def test_create_index_uses_config_num_bits(self) -> None:
        """create_index passes config num_bits to LanceDB."""
        from arrow_lake.config import VectorSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(row_count=1000, has_index=True)
        storage.open_dataset.return_value = mock_table

        config = VectorSearchConfig(num_bits=4)
        bridge = VectorSearchBridge(storage, config=config)
        bridge.create_index("test_ds")

        call_kwargs = mock_table.create_index.call_args[1]
        assert call_kwargs["num_bits"] == 4

    def test_create_index_uses_config_num_partitions_base(self) -> None:
        """Auto-partition uses config num_partitions as base."""
        from arrow_lake.config import VectorSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(row_count=10000, has_index=True)
        storage.open_dataset.return_value = mock_table

        config = VectorSearchConfig(num_partitions=128)
        bridge = VectorSearchBridge(storage, config=config)
        bridge.create_index("test_ds")

        call_kwargs = mock_table.create_index.call_args[1]
        assert call_kwargs["num_partitions"] == 128

    def test_search_uses_config_default_top_k(self) -> None:
        """search uses config default_top_k when not specified."""
        from arrow_lake.config import VectorSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table()
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_query_builder()
        mock_table.search.return_value = builder

        config = VectorSearchConfig(default_top_k=5)
        bridge = VectorSearchBridge(storage, config=config)
        result = bridge.search("test_ds", [0.0] * 384)

        assert result.top_k == 5
        builder.limit.assert_called_once_with(5)

    def test_search_uses_config_metric(self) -> None:
        """search uses config metric when not specified."""
        from arrow_lake.config import VectorSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table()
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_query_builder()
        mock_table.search.return_value = builder

        config = VectorSearchConfig(metric="l2")
        bridge = VectorSearchBridge(storage, config=config)
        bridge.search("test_ds", [0.0] * 384)

        builder.distance_type.assert_called_once_with("l2")

    def test_search_max_nprobes_clamping(self) -> None:
        """search clamps nprobes to config max_nprobes."""
        from arrow_lake.config import VectorSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table()
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_query_builder()
        mock_table.search.return_value = builder

        config = VectorSearchConfig(max_nprobes=10)
        bridge = VectorSearchBridge(storage, config=config)
        bridge.search("test_ds", [0.0] * 384, nprobes=999)

        builder.nprobes.assert_called_once_with(10)

    def test_explicit_param_overrides_config(self) -> None:
        """Explicit parameters override config values."""
        from arrow_lake.config import VectorSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table()
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_query_builder()
        mock_table.search.return_value = builder

        config = VectorSearchConfig(default_top_k=5, max_nprobes=10)
        bridge = VectorSearchBridge(storage, config=config)
        result = bridge.search("test_ds", [0.0] * 384, top_k=20, nprobes=3)

        assert result.top_k == 20
        builder.limit.assert_called_once_with(20)
        builder.nprobes.assert_called_once_with(3)

    def test_no_config_uses_defaults(self) -> None:
        """Bridge without config uses VectorSearchConfig defaults."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table()
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_query_builder()
        mock_table.search.return_value = builder

        bridge = VectorSearchBridge(storage)  # No config
        result = bridge.search("test_ds", [0.0] * 384)

        assert result.top_k == 10  # Default
