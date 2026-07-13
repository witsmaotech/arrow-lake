"""Unit tests for ``_LakeEmbedderAdapter`` (hyper-extract embedder wrapper).

Covers both dispatch branches (``ApiEmbeddingEncoder.encode`` vs
``LocalEmbeddingEncoder.encode_to_vectors``) plus edge cases, using fake
encoders so no real embedding model is loaded (fast, hermetic).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest
from langchain_core.embeddings import Embeddings

from arrow_lake.embed.encoder import EmbeddingBatch
from arrow_lake.knowledge_graph._he_embedder import _LakeEmbedderAdapter


@dataclass
class _FakeApiEncoder:
    """Mimics ``ApiEmbeddingEncoder.encode(list[str]) -> EmbeddingBatch``."""

    dim: int = 8

    def encode(self, texts: list[str]) -> EmbeddingBatch:
        return EmbeddingBatch(
            embeddings=np.full((len(texts), self.dim), 0.5, dtype=np.float32),
            null_mask=tuple(False for _ in texts),
        )


class _FakeLocalEncoder:
    """Mimics ``LocalEmbeddingEncoder.encode_to_vectors(table, col)``."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    def encode_to_vectors(self, table, column: str = "text_content"):
        n = table.num_rows
        return np.full((n, self.dim), 0.25, dtype=np.float32), self.dim


class TestLakeEmbedderAdapterApiPath:
    """ApiEmbeddingEncoder branch: dispatches via encode(list[str])."""

    def test_embed_query_returns_vector_of_expected_dim(self):
        # Arrange
        adapter = _LakeEmbedderAdapter(_FakeApiEncoder(dim=8))
        # Act
        vec = adapter.embed_query("hello")
        # Assert
        assert isinstance(vec, list)
        assert len(vec) == 8
        assert all(v == pytest.approx(0.5) for v in vec)

    def test_embed_documents_returns_one_vector_per_text(self):
        # Arrange
        adapter = _LakeEmbedderAdapter(_FakeApiEncoder(dim=4))
        # Act
        out = adapter.embed_documents(["a", "b", "c"])
        # Assert
        assert len(out) == 3
        assert all(len(v) == 4 for v in out)


class TestLakeEmbedderAdapterLocalPath:
    """LocalEmbeddingEncoder / Daft branch: dispatches via encode_to_vectors."""

    def test_embed_query_routes_to_encode_to_vectors(self):
        # Arrange
        adapter = _LakeEmbedderAdapter(_FakeLocalEncoder(dim=8))
        # Act
        vec = adapter.embed_query("hello")
        # Assert
        assert len(vec) == 8
        assert all(v == pytest.approx(0.25) for v in vec)

    def test_embed_documents_shape_matches_input_count(self):
        # Arrange
        adapter = _LakeEmbedderAdapter(_FakeLocalEncoder(dim=6))
        # Act
        out = adapter.embed_documents(["x", "y"])
        # Assert
        assert len(out) == 2
        assert all(len(v) == 6 for v in out)


class TestLakeEmbedderAdapterEdgeCases:
    def test_empty_texts_returns_empty_list(self):
        adapter = _LakeEmbedderAdapter(_FakeApiEncoder())
        assert adapter.embed_documents([]) == []

    def test_unsupported_encoder_raises_typeerror(self):
        adapter = _LakeEmbedderAdapter(object())
        with pytest.raises(TypeError):
            adapter.embed_query("anything")

    def test_satisfies_langchain_embeddings_protocol(self):
        adapter = _LakeEmbedderAdapter(_FakeApiEncoder())
        assert isinstance(adapter, Embeddings)
