"""Unit tests for arrow_lake.embed.daft_encoder — Daft repositioning Sprint 2."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pyarrow as pa
import pytest
from arrow_lake.embed.daft_encoder import DaftBatchEncoder


@pytest.fixture
def sample_table() -> pa.Table:
    return pa.table({"text_content": ["hello", "world", "test"]})


@pytest.fixture
def encoder() -> DaftBatchEncoder:
    return DaftBatchEncoder(model="test-model", provider="transformers", num_partitions=2)


class TestDaftBatchEncoderInit:
    def test_defaults(self) -> None:
        enc = DaftBatchEncoder()
        assert enc._model == "Qwen/Qwen3-Embedding-0.6B"
        assert enc._provider == "transformers"
        assert enc._num_partitions == 4

    def test_custom_params(self) -> None:
        enc = DaftBatchEncoder(model="custom", provider="openai", num_partitions=8)
        assert enc._model == "custom"
        assert enc._num_partitions == 8


class TestEncodeColumnEmpty:
    def test_empty_table_returns_zero(self) -> None:
        enc = DaftBatchEncoder()
        empty = pa.table({"text_content": pa.array([], type=pa.string())})
        result = enc.encode_column(empty)
        assert result.total_rows == 0
        assert result.embedded_rows == 0
        assert result.embedding_dim == 0

    def test_missing_column_raises(self) -> None:
        enc = DaftBatchEncoder()
        table = pa.table({"other": ["a"]})
        with pytest.raises(ValueError, match="not found"):
            enc.encode_column(table, column="text_content")


class TestEncodeColumnMocked:
    """Test encode_column with mocked Daft DataFrame."""

    @patch("arrow_lake.embed.daft_encoder.DaftBatchEncoder._embed_expr")
    def test_encode_basic(self, mock_embed: MagicMock, sample_table: pa.Table) -> None:
        mock_embed.return_value = "mock_expr"

        emb_data = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
        result_table = pa.table({"text_content_embedding": emb_data})

        mock_df = MagicMock()
        mock_df.into_partitions.return_value = mock_df
        mock_df.with_column.return_value = mock_df
        mock_df.select.return_value = mock_df
        mock_df.to_arrow.return_value = result_table

        with patch("daft.from_arrow", return_value=mock_df):
            enc = DaftBatchEncoder(num_partitions=2)
            result = enc.encode_column(sample_table)

        assert result.total_rows == 3
        assert result.embedded_rows == 3
        assert result.null_rows == 0
        assert result.embedding_dim == 2

    @patch("arrow_lake.embed.daft_encoder.DaftBatchEncoder._embed_expr")
    def test_encode_with_nulls(self, mock_embed: MagicMock) -> None:
        mock_embed.return_value = "mock_expr"

        table = pa.table({"text_content": ["hello", None, "world"]})
        emb_data = [[0.1, 0.2], None, [0.3, 0.4]]
        result_table = pa.table({"text_content_embedding": emb_data})

        mock_df = MagicMock()
        mock_df.into_partitions.return_value = mock_df
        mock_df.with_column.return_value = mock_df
        mock_df.select.return_value = mock_df
        mock_df.to_arrow.return_value = result_table

        with patch("daft.from_arrow", return_value=mock_df):
            enc = DaftBatchEncoder()
            result = enc.encode_column(table)

        assert result.total_rows == 3
        assert result.embedded_rows == 2
        assert result.null_rows == 1


class TestEncodeToVectors:
    @patch("arrow_lake.embed.daft_encoder.DaftBatchEncoder._embed_expr")
    def test_returns_ndarray(self, mock_embed: MagicMock) -> None:
        mock_embed.return_value = "mock_expr"

        table = pa.table({"text_content": ["a", "b"]})
        emb_data = [[1.0, 2.0], [3.0, 4.0]]
        result_table = pa.table({"text_content_embedding": emb_data})

        mock_df = MagicMock()
        mock_df.into_partitions.return_value = mock_df
        mock_df.with_column.return_value = mock_df
        mock_df.select.return_value = mock_df
        mock_df.to_arrow.return_value = result_table

        with patch("daft.from_arrow", return_value=mock_df):
            enc = DaftBatchEncoder()
            vectors, dim = enc.encode_to_vectors(table)

        assert isinstance(vectors, np.ndarray)
        assert dim == 2
        assert vectors.shape == (2, 2)


class TestInferDim:
    def test_list_array(self) -> None:
        arr = pa.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
        chunked = pa.chunked_array([arr])
        assert DaftBatchEncoder._infer_dim(chunked) == 3

    def test_all_null(self) -> None:
        arr = pa.array([None, None], type=pa.list_(pa.float32()))
        chunked = pa.chunked_array([arr])
        assert DaftBatchEncoder._infer_dim(chunked) == 0
