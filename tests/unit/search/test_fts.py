"""Tests for arrow_lake.query.fts — Story 5.2.

Tests FullTextSearchBridge:
- DTO frozen dataclass
- create_index (success, non-text column, replace)
- search (results, empty, where filter, injection prevention, no index)
- jieba Chinese tokenization integration
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from arrow_lake.exceptions import QueryError
from arrow_lake.query.fts import FullTextSearchBridge, FullTextSearchResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_storage() -> MagicMock:
    """Create a mock LanceStorageManager."""
    storage = MagicMock()
    storage.dataset_uri.return_value = "/tmp/test.lance"
    storage.storage_options = None
    return storage


def _make_mock_lance_table(
    *,
    has_text_column: bool = True,
    is_large_string: bool = False,
    has_fts_segmented: bool = True,
) -> MagicMock:
    """Create a mock LanceDB table with text column."""
    table = MagicMock()

    names = ["text_content", "modality", "source"]
    if has_fts_segmented:
        names.append("_fts_segmented")

    if has_text_column:
        text_type = pa.large_string() if is_large_string else pa.string()
        text_field = MagicMock()
        text_field.name = "text_content"
        text_field.type = text_type
        schema = MagicMock()
        schema.names = names
        schema.field.side_effect = lambda name: {
            "text_content": text_field,
        }.get(name, MagicMock())
        table.schema = schema
    else:
        table.schema = MagicMock(names=["modality", "source"])

    return table


def _make_mock_fts_builder(result_table: pa.Table | None = None) -> MagicMock:
    """Create a mock LanceDB FTS query builder with fluent API."""
    builder = MagicMock()

    if result_table is None:
        result_table = pa.table(
            {
                "text_content": [],
                "modality": [],
                "_score": [],
            }
        )

    builder.to_arrow.return_value = result_table
    # Fluent API: each method returns self
    builder.where.return_value = builder
    builder.limit.return_value = builder
    return builder


def _make_mock_lance_table_no_jieba(
    *,
    has_text_column: bool = True,
    is_large_string: bool = False,
) -> MagicMock:
    """Create a mock table WITHOUT _fts_segmented column (tokenizer_type=default)."""
    table = MagicMock()

    if has_text_column:
        text_type = pa.large_string() if is_large_string else pa.string()
        text_field = MagicMock()
        text_field.name = "text_content"
        text_field.type = text_type
        schema = MagicMock()
        schema.names = ["text_content", "modality", "source"]
        schema.field.side_effect = lambda name: {
            "text_content": text_field,
        }.get(name, MagicMock())
        table.schema = schema
    else:
        table.schema = MagicMock(names=["modality", "source"])

    return table


# ---------------------------------------------------------------------------
# DTO Tests
# ---------------------------------------------------------------------------


class TestFullTextSearchResult:
    """Test FullTextSearchResult frozen dataclass."""

    def test_is_frozen(self) -> None:
        table = pa.table({"_score": [1.5, 0.8]})
        result = FullTextSearchResult(
            table=table,
            row_count=2,
            query="test query",
            top_k=10,
            fts_column="text_content",
            max_score=1.5,
        )
        with pytest.raises(FrozenInstanceError):
            result.row_count = 5  # type: ignore[misc]

    def test_all_fields_accessible(self) -> None:
        table = pa.table({"_score": [2.3]})
        result = FullTextSearchResult(
            table=table,
            row_count=1,
            query="hello",
            top_k=5,
            fts_column="text_content",
            max_score=2.3,
        )
        assert result.row_count == 1
        assert result.query == "hello"
        assert result.top_k == 5
        assert result.fts_column == "text_content"
        assert result.max_score == 2.3

    def test_max_score_none_for_empty(self) -> None:
        table = pa.table({"_score": []})
        result = FullTextSearchResult(
            table=table,
            row_count=0,
            query="nothing",
            top_k=10,
            fts_column="text_content",
            max_score=None,
        )
        assert result.max_score is None


# ---------------------------------------------------------------------------
# FullTextSearchBridge.create_index Tests
# ---------------------------------------------------------------------------


class TestCreateIndex:
    """Test FullTextSearchBridge.create_index."""

    def test_create_index_success_default_tokenizer(self) -> None:
        """Happy path with tokenizer_type=default: no segmented column."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        bridge.create_index("test_ds")

        mock_table.create_index.assert_called_once()
        # create_index(positional column, config=FTS(), replace=)
        assert mock_table.create_index.call_args[0][0] == "text_content"

    def test_create_index_success_jieba_tokenizer_ignored(self) -> None:
        """tokenizer_type=jieba is accepted but icu native FTS is used regardless
        (lancedb 0.36 removed tantivy; ICU handles CJK inline)."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        config = FullTextSearchConfig(tokenizer_type="jieba")
        bridge = FullTextSearchBridge(storage, config=config)
        bridge.create_index("test_ds")

        # Native FTS indexes the original column (icu), not a _fts_segmented column
        mock_table.create_index.assert_called_once()
        assert mock_table.create_index.call_args[0][0] == "text_content"

    def test_create_index_large_string(self) -> None:
        """Create FTS index on large_string column."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba(is_large_string=True)
        storage.open_dataset.return_value = mock_table

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        bridge.create_index("test_ds")

        mock_table.create_index.assert_called_once()

    def test_create_index_custom_column(self) -> None:
        """Create FTS index on a custom text column."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        # Override to have a custom column
        custom_field = MagicMock()
        custom_field.name = "description"
        custom_field.type = pa.string()
        mock_table.schema.names = ["description", "modality"]
        mock_table.schema.field.side_effect = lambda name: {
            "description": custom_field,
        }.get(name, MagicMock())
        storage.open_dataset.return_value = mock_table

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        bridge.create_index("test_ds", fts_column="description")

        assert mock_table.create_index.call_args[0][0] == "description"

    def test_create_index_replace(self) -> None:
        """replace=True replaces existing index."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        bridge.create_index("test_ds", replace=True)

        call_kwargs = mock_table.create_index.call_args[1]
        assert call_kwargs["replace"] is True

    def test_create_index_no_replace(self) -> None:
        """replace=False does not replace existing index."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        bridge.create_index("test_ds", replace=False)

        call_kwargs = mock_table.create_index.call_args[1]
        assert call_kwargs["replace"] is False

    def test_create_index_column_not_found(self) -> None:
        """Raise FTS_INDEX_FAILED when text column missing."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba(has_text_column=False)
        storage.open_dataset.return_value = mock_table

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        with pytest.raises(QueryError, match="not found"):
            bridge.create_index("test_ds", fts_column="nonexistent")

    def test_create_index_non_text_column(self) -> None:
        """Raise FTS_INDEX_FAILED when column is not text type."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        # Override to have integer column
        int_field = MagicMock()
        int_field.name = "text_content"
        int_field.type = pa.int32()
        mock_table.schema.field.side_effect = lambda name: {
            "text_content": int_field,
        }.get(name, MagicMock())
        storage.open_dataset.return_value = mock_table

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        with pytest.raises(QueryError, match="not a text column"):
            bridge.create_index("test_ds")

    def test_create_index_failure(self) -> None:
        """Raise FTS_INDEX_FAILED on create_index exception."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        mock_table.create_index.side_effect = RuntimeError("Lance error")
        storage.open_dataset.return_value = mock_table

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        with pytest.raises(QueryError, match="Failed to create FTS index"):
            bridge.create_index("test_ds")


# ---------------------------------------------------------------------------
# FullTextSearchBridge.search Tests
# ---------------------------------------------------------------------------


class TestSearch:
    """Test FullTextSearchBridge.search."""

    def test_search_returns_results_default_tokenizer(self) -> None:
        """Happy path with tokenizer_type=default."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        result_table = pa.table(
            {
                "text_content": ["hello world", "hello python"],
                "modality": ["text", "text"],
                "_score": [2.5, 1.3],
            }
        )
        builder = _make_mock_fts_builder(result_table)
        mock_table.search.return_value = builder

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        result = bridge.search("test_ds", "hello")

        assert isinstance(result, FullTextSearchResult)
        assert result.row_count == 2
        assert result.query == "hello"
        assert result.fts_column == "text_content"
        assert result.max_score == 2.5
        assert result.top_k == 10
        # With default tokenizer, query is NOT segmented
        mock_table.search.assert_called_once_with(
            query="hello", query_type="fts", fts_columns="text_content"
        )

    # test_search_returns_results_jieba / test_search_jieba_hides_segmented_column
    # removed in v1.9.7 — jieba/_fts_segmented path deleted; native FTS (ICU)
    # searches the original column inline (covered by test_search_*_default_tokenizer).

    def test_search_score_column_present(self) -> None:
        """Result table includes _score column."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        result_table = pa.table(
            {
                "text_content": ["match"],
                "_score": [1.0],
            }
        )
        builder = _make_mock_fts_builder(result_table)
        mock_table.search.return_value = builder

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        result = bridge.search("test_ds", "match")

        assert "_score" in result.table.column_names

    def test_search_with_where_filter(self) -> None:
        """Where clause is passed to the query builder."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_fts_builder()
        mock_table.search.return_value = builder

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        bridge.search("test_ds", "hello", where="modality = 'text'")

        builder.where.assert_called_once_with("modality = 'text'")

    def test_search_empty_results(self) -> None:
        """Empty results return 0-row table with max_score=None."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        result_table = pa.table(
            {
                "text_content": [],
                "modality": [],
                "_score": [],
            }
        )
        builder = _make_mock_fts_builder(result_table)
        mock_table.search.return_value = builder

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        result = bridge.search("test_ds", "nonexistent_query_xyz")

        assert result.row_count == 0
        assert result.max_score is None
        assert result.table.num_rows == 0

    def test_search_custom_top_k(self) -> None:
        """Custom top_k is passed to the query builder."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_fts_builder()
        mock_table.search.return_value = builder

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        result = bridge.search("test_ds", "hello", top_k=5)

        assert result.top_k == 5
        builder.limit.assert_called_once_with(5)

    def test_search_custom_fts_column(self) -> None:
        """Custom fts_column is used for search."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        # Add "description" to schema names
        mock_table.schema.names = ["text_content", "description", "modality"]
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_fts_builder()
        mock_table.search.return_value = builder

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        result = bridge.search("test_ds", "hello", fts_column="description")

        assert result.fts_column == "description"
        mock_table.search.assert_called_once_with(
            query="hello", query_type="fts", fts_columns="description"
        )

    def test_search_empty_query_raises(self) -> None:
        """Raise FTS_SEARCH_FAILED when query is empty."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)

        with pytest.raises(QueryError, match="must not be empty"):
            bridge.search("test_ds", "")

        with pytest.raises(QueryError, match="must not be empty"):
            bridge.search("test_ds", "   ")

    def test_search_invalid_top_k_raises(self) -> None:
        """Raise FTS_SEARCH_FAILED when top_k < 1."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        with pytest.raises(QueryError, match="top_k must be >= 1"):
            bridge.search("test_ds", "hello", top_k=0)

        with pytest.raises(QueryError, match="top_k must be >= 1"):
            bridge.search("test_ds", "hello", top_k=-1)

    def test_search_column_not_found(self) -> None:
        """Raise FTS_SEARCH_FAILED when text column missing."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba(has_text_column=False)
        storage.open_dataset.return_value = mock_table

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        with pytest.raises(QueryError, match="not found"):
            bridge.search("test_ds", "hello")

    def test_search_failure(self) -> None:
        """Raise FTS_SEARCH_FAILED on search exception."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        # Mock search() to succeed but to_arrow() to fail
        builder = MagicMock()
        builder.to_arrow.side_effect = RuntimeError("Lance error")
        builder.where.return_value = builder
        builder.limit.return_value = builder
        mock_table.search.return_value = builder

        config = FullTextSearchConfig(tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        with pytest.raises(QueryError, match="FTS search failed"):
            bridge.search("test_ds", "hello")


# ---------------------------------------------------------------------------
# Where clause injection prevention
# ---------------------------------------------------------------------------


class TestWhereClauseValidation:
    """Test _validate_where_clause injection prevention."""

    def test_safe_where_clause_passes(self) -> None:
        """Safe filter expressions should not raise."""
        FullTextSearchBridge._validate_where_clause("modality = 'text'")
        FullTextSearchBridge._validate_where_clause("source = 'src-0' AND modality = 'text'")

    def test_dangerous_keywords_blocked(self) -> None:
        """Dangerous SQL keywords should raise FTS_SEARCH_FAILED."""
        for keyword in ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "TRUNCATE"]:
            with pytest.raises(QueryError, match="dangerous"):
                FullTextSearchBridge._validate_where_clause(f"{keyword} TABLE")

    def test_union_blocked(self) -> None:
        with pytest.raises(QueryError, match="dangerous"):
            FullTextSearchBridge._validate_where_clause("1=1 UNION SELECT *")

    def test_case_insensitive_detection(self) -> None:
        with pytest.raises(QueryError, match="dangerous"):
            FullTextSearchBridge._validate_where_clause("drop table users")


# ---------------------------------------------------------------------------
# Config-driven defaults
# ---------------------------------------------------------------------------


class TestConfigDrivenDefaults:
    """Test FullTextSearchConfig drives bridge defaults."""

    def test_search_uses_config_default_top_k(self) -> None:
        """search uses config default_top_k when not specified."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_fts_builder()
        mock_table.search.return_value = builder

        config = FullTextSearchConfig(default_top_k=5, tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        result = bridge.search("test_ds", "hello")

        assert result.top_k == 5
        builder.limit.assert_called_once_with(5)

    def test_search_uses_config_fts_column(self) -> None:
        """search uses config fts_column when not specified."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        # Override to have custom column
        custom_field = MagicMock()
        custom_field.name = "body"
        custom_field.type = pa.string()
        mock_table.schema.names = ["body", "modality"]
        mock_table.schema.field.side_effect = lambda name: {
            "body": custom_field,
        }.get(name, MagicMock())
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_fts_builder()
        mock_table.search.return_value = builder

        config = FullTextSearchConfig(fts_column="body", tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        result = bridge.search("test_ds", "hello")

        assert result.fts_column == "body"
        mock_table.search.assert_called_once_with(
            query="hello", query_type="fts", fts_columns="body"
        )

    def test_create_index_uses_config_stem(self) -> None:
        """create_index passes config stem to LanceDB."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        config = FullTextSearchConfig(stem=False, tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        bridge.create_index("test_ds")

        cfg = mock_table.create_index.call_args[1]["config"]
        assert cfg.stem is False

    def test_explicit_param_overrides_config(self) -> None:
        """Explicit parameters override config values."""
        from arrow_lake.config import FullTextSearchConfig

        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table_no_jieba()
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_fts_builder()
        mock_table.search.return_value = builder

        config = FullTextSearchConfig(default_top_k=5, tokenizer_type="default")
        bridge = FullTextSearchBridge(storage, config=config)
        result = bridge.search("test_ds", "hello", top_k=20)

        assert result.top_k == 20
        builder.limit.assert_called_once_with(20)

    def test_no_config_uses_defaults(self) -> None:
        """Bridge without config uses FullTextSearchConfig defaults."""
        storage = _make_mock_storage()
        mock_table = _make_mock_lance_table(has_fts_segmented=False)
        storage.open_dataset.return_value = mock_table

        builder = _make_mock_fts_builder()
        mock_table.search.return_value = builder

        bridge = FullTextSearchBridge(storage)  # No config — defaults
        result = bridge.search("test_ds", "hello")

        assert result.top_k == 10  # Default
        # Searches the original text column (icu tokenizes inline)
        call_kwargs = mock_table.search.call_args[1]
        assert call_kwargs["fts_columns"] == "text_content"


# _add_segmented_column tests removed in v1.9.7 — jieba pre-tokenization path
# deleted; lancedb 0.36 native FTS (ICU) needs no _fts_segmented column.
