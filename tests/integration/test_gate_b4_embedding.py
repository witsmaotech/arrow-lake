"""Gate B4: Text embedding end-to-end validation.

Validates LocalEmbeddingEncoder produces fixed_size_list<float>[1024]:
- Encode text column to embeddings
- Write embeddings to Lance dataset
- Read back and verify dimensions
- Verify null/empty text handling
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from arrow_lake.ingest.storage import LanceStorageManager


class TestGateB4TextEmbedding:
    """Validate text embedding pipeline end-to-end."""

    @pytest.mark.skip(reason="Requires HuggingFace model download — can timeout in CI")
    def test_encode_produces_correct_dimensions(self) -> None:
        """Embedding vectors have dimension 1024."""
        from arrow_lake.embed.encoder import LocalEmbeddingEncoder

        encoder = LocalEmbeddingEncoder(model_name="Qwen/Qwen3-Embedding-0.6B")
        table = pa.table(
            {
                "text_content": [
                    "Hello world",
                    "Machine learning is great",
                    "Data lakehouse architecture",
                ],
            }
        )

        result = encoder.encode_column(table, column="text_content")
        assert result.total_rows == 3
        assert result.embedded_rows == 3
        assert result.null_rows == 0
        assert result.embedding_dim == 1024

    def test_null_text_handling(self) -> None:
        """NULL texts produce null_mask=True and are skipped."""
        from arrow_lake.embed.encoder import LocalEmbeddingEncoder

        encoder = LocalEmbeddingEncoder(model_name="Qwen/Qwen3-Embedding-0.6B")
        table = pa.table(
            {
                "text_content": ["valid text", None, "another valid", None],
            },
            schema=pa.schema(
                [
                    ("text_content", pa.string()),
                ]
            ),
        )

        result = encoder.encode_column(table, column="text_content")
        assert result.total_rows == 4
        assert result.embedded_rows == 2
        assert result.null_rows == 2

    def test_empty_table_returns_zero_embedded(self) -> None:
        """Empty table returns zero embedded rows."""
        from arrow_lake.embed.encoder import LocalEmbeddingEncoder

        encoder = LocalEmbeddingEncoder(model_name="Qwen/Qwen3-Embedding-0.6B")
        table = pa.table({"text_content": []})

        result = encoder.encode_column(table, column="text_content")
        assert result.total_rows == 0
        assert result.embedded_rows == 0

    @pytest.mark.skip(reason="Requires HuggingFace model download — can timeout in CI")
    def test_embeddings_writable_to_lance(self, tmp_path: Path) -> None:
        """Embedding column can be written to Lance and read back."""
        from arrow_lake.embed.encoder import LocalEmbeddingEncoder

        encoder = LocalEmbeddingEncoder(model_name="Qwen/Qwen3-Embedding-0.6B")

        # Create source table
        texts = [f"Document about topic {i}" for i in range(10)]
        source_table = pa.table({"text_content": texts})

        # Encode
        result = encoder.encode_column(source_table, column="text_content")
        assert result.embedded_rows == 10
        assert result.embedding_dim == 1024

        # Get actual embeddings from the encoder
        model = encoder._load_model()
        embeddings = np.asarray(
            model.encode(texts, normalize_embeddings=True),
            dtype=np.float32,
        )

        # Build embedding column with correct Arrow type
        embedding_type = pa.list_(pa.float32(), 1024)
        embedding_col = pa.array(
            [emb.tolist() for emb in embeddings],
            type=embedding_type,
        )

        # Create table with embeddings and write to Lance
        storage = LanceStorageManager(str(tmp_path / "lance_data"))
        embed_table = source_table.append_column(
            result.vector_column,
            embedding_col,
        )
        storage.create_dataset("embedded_docs", embed_table)

        # Read back and verify
        loaded = storage.read_dataset("embedded_docs")
        assert loaded.num_rows == 10
        assert result.vector_column in loaded.column_names

        # Verify embedding type is fixed_size_list
        field = loaded.schema.field(result.vector_column)
        assert pa.types.is_fixed_size_list(field.type)
        assert pa.types.is_float32(field.type.value_type)
        assert field.type.list_size == 1024

    def test_all_null_column(self) -> None:
        """Column with all NULLs returns zero embedded rows."""
        from arrow_lake.embed.encoder import LocalEmbeddingEncoder

        encoder = LocalEmbeddingEncoder(model_name="Qwen/Qwen3-Embedding-0.6B")
        table = pa.table(
            {
                "text_content": pa.array([None, None, None], type=pa.string()),
            }
        )

        result = encoder.encode_column(table, column="text_content")
        assert result.total_rows == 3
        assert result.embedded_rows == 0
        assert result.null_rows == 3
