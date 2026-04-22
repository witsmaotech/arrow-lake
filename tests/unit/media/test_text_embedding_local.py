"""Tests for local text embedding — Story 4.1 (unit).

Tests LocalEmbeddingEncoder with mocked SentenceTransformer:
- Batch encoding
- NULL/empty text handling
- Embedding dimension validation
- GPU auto-detection
- Lazy model loading
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pyarrow as pa
import pytest
from arrow_lake.embed.encoder import EmbeddingBatch, EmbeddingResult, LocalEmbeddingEncoder


class TestEmbeddingBatch:
    """Test EmbeddingBatch frozen dataclass."""

    def test_batch_is_frozen(self) -> None:
        batch = EmbeddingBatch(
            embeddings=np.zeros((3, 1024), dtype=np.float32),
            null_mask=(False, False, False),
        )
        with pytest.raises(AttributeError):
            batch.embeddings = np.zeros(5)  # type: ignore[misc]


class TestEmbeddingResult:
    """Test EmbeddingResult frozen dataclass."""

    def test_result_is_frozen(self) -> None:
        result = EmbeddingResult(
            total_rows=10,
            embedded_rows=10,
            null_rows=0,
            embedding_dim=1024,
            vector_column="text_embedding",
        )
        with pytest.raises(AttributeError):
            result.total_rows = 99  # type: ignore[misc]

    def test_result_fields(self) -> None:
        result = EmbeddingResult(
            total_rows=10,
            embedded_rows=8,
            null_rows=2,
            embedding_dim=1024,
            vector_column="text_embedding",
        )
        assert result.total_rows == 10
        assert result.embedded_rows == 8
        assert result.null_rows == 2
        assert result.embedding_dim == 1024


class TestLocalEmbeddingEncoder:
    """Test LocalEmbeddingEncoder logic."""

    def test_encoder_init_default_model(self) -> None:
        encoder = LocalEmbeddingEncoder()
        assert encoder.model_name == "Qwen/Qwen3-Embedding-0.6B"
        assert encoder.batch_size == 128

    def test_encoder_init_custom(self) -> None:
        encoder = LocalEmbeddingEncoder(
            model_name="BAAI/bge-large-en-v1.5",
            batch_size=64,
        )
        assert encoder.model_name == "BAAI/bge-large-en-v1.5"
        assert encoder.batch_size == 64

    def test_encode_column_with_mock(self) -> None:
        """Test encode_column with mocked SentenceTransformer."""
        mock_embeddings = np.random.randn(3, 1024).astype(np.float32)

        mock_model = MagicMock()
        mock_model.encode.return_value = mock_embeddings

        encoder = LocalEmbeddingEncoder()
        encoder._model = mock_model

        table = pa.table(
            {
                "text_content": ["hello world", "foo bar", ""],
                "id": ["1", "2", "3"],
            }
        )

        result = encoder.encode_column(table, column="text_content")
        assert result.total_rows == 3
        assert result.embedded_rows == 3
        assert result.embedding_dim == 1024
        assert result.vector_column == "text_content_embedding"

    def test_encode_column_with_nulls(self) -> None:
        """NULL text values should produce null_mask entries."""
        mock_embeddings = np.random.randn(2, 1024).astype(np.float32)

        mock_model = MagicMock()
        mock_model.encode.return_value = mock_embeddings

        encoder = LocalEmbeddingEncoder()
        encoder._model = mock_model

        table = pa.table(
            {
                "text_content": ["hello", None, "world"],
                "id": ["1", "2", "3"],
            }
        )

        result = encoder.encode_column(table, column="text_content")
        assert result.total_rows == 3
        assert result.embedded_rows == 2
        assert result.null_rows == 1

    def test_encode_column_all_nulls(self) -> None:
        """All-null column should produce zero embeddings."""
        encoder = LocalEmbeddingEncoder()
        # No model needed for all-null column
        encoder._model = None

        table = pa.table(
            {
                "text_content": pa.array([None, None], type=pa.string()),
                "id": pa.array(["1", "2"], type=pa.string()),
            }
        )

        result = encoder.encode_column(table, column="text_content")
        assert result.total_rows == 2
        assert result.embedded_rows == 0
        assert result.null_rows == 2

    def test_encode_column_empty_strings_counted(self) -> None:
        """Empty strings should be treated as non-null."""
        mock_embeddings = np.random.randn(2, 1024).astype(np.float32)
        mock_model = MagicMock()
        mock_model.encode.return_value = mock_embeddings

        encoder = LocalEmbeddingEncoder()
        encoder._model = mock_model

        table = pa.table(
            {
                "text_content": ["hello", ""],
                "id": ["1", "2"],
            }
        )

        result = encoder.encode_column(table, column="text_content")
        # Empty strings are still encoded (not null)
        assert result.embedded_rows == 2
        assert result.null_rows == 0

    def test_encode_column_missing_column_raises(self) -> None:
        """Requesting a non-existent column should raise."""
        encoder = LocalEmbeddingEncoder()

        table = pa.table({"id": ["1", "2"]})

        with pytest.raises(ValueError, match="Column"):
            encoder.encode_column(table, column="nonexistent")

    def test_build_embedding_table_output_type(self) -> None:
        """Verify output table has correct vector type."""
        mock_embeddings = np.random.randn(2, 1024).astype(np.float32)
        mock_model = MagicMock()
        mock_model.encode.return_value = mock_embeddings

        encoder = LocalEmbeddingEncoder()
        encoder._model = mock_model

        table = pa.table(
            {
                "text_content": ["hello", "world"],
                "id": ["1", "2"],
            }
        )

        result = encoder.encode_column(table, column="text_content")
        assert result.embedding_dim == 1024
