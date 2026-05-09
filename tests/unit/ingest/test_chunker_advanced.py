"""Unit tests for advanced chunking strategies (semchunk, chonkie).

All tests use mock to avoid requiring actual library installation.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.config._enums import ChunkStrategy
from arrow_lake.config.document import DocumentConfig
from arrow_lake.ingest.chunker import DocumentChunker


def _mock_module(name: str) -> MagicMock:
    """Create a mock module and register it in sys.modules."""
    mod = MagicMock()
    sys.modules[name] = mod
    return mod


class TestSemchunkGracefulDegradation:
    """Test graceful fallback when semchunk is not installed."""

    @patch("arrow_lake.ingest.chunker._SEMCHUNK_AVAILABLE", False)
    def test_semchunk_falls_back_to_recursive(self, caplog):
        chunker = DocumentChunker(strategy=ChunkStrategy.SEMCHUNK, chunk_size=128)
        assert chunker._strategy == ChunkStrategy.RECURSIVE
        assert "falling back" in caplog.text.lower()

    def test_semchunk_delegates_to_semchunk(self):
        mock_semchunk = _mock_module("semchunk")
        mock_semchunk.chunk.return_value = ["chunk one", "chunk two"]
        try:
            import importlib

            from arrow_lake.ingest import chunker as chunker_mod
            importlib.reload(chunker_mod)

            chunker = chunker_mod.DocumentChunker(
                strategy=ChunkStrategy.SEMCHUNK, chunk_size=128,
            )
            pages = [(1, "some long text"), (2, "more text")]
            chunks = chunker.chunk(pages)
            assert len(chunks) == 4
            assert all(isinstance(c, chunker_mod.Chunk) for c in chunks)
            assert chunks[0].page_number == 1
            assert chunks[2].page_number == 2
            mock_semchunk.chunk.assert_called()
        finally:
            del sys.modules["semchunk"]
            importlib.reload(chunker_mod)

    def test_semchunk_passes_chunk_size(self):
        mock_semchunk = _mock_module("semchunk")
        mock_semchunk.chunk.return_value = ["text"]
        try:
            import importlib

            from arrow_lake.ingest import chunker as chunker_mod
            importlib.reload(chunker_mod)

            chunker = chunker_mod.DocumentChunker(
                strategy=ChunkStrategy.SEMCHUNK,
                chunk_size=256,
                chunk_overlap=32,
            )
            chunker.chunk([(1, "text")])
            call_kwargs = mock_semchunk.chunk.call_args
            assert call_kwargs.kwargs["chunk_size"] == 256
        finally:
            del sys.modules["semchunk"]
            importlib.reload(chunker_mod)


class TestChonkieGracefulDegradation:
    """Test graceful fallback when chonkie is not installed."""

    @patch("arrow_lake.ingest.chunker._CHONKIE_AVAILABLE", False)
    def test_chonkie_token_falls_back_to_recursive(self, caplog):
        chunker = DocumentChunker(strategy=ChunkStrategy.CHONKIE_TOKEN)
        assert chunker._strategy == ChunkStrategy.RECURSIVE
        assert "falling back" in caplog.text.lower()

    @patch("arrow_lake.ingest.chunker._CHONKIE_AVAILABLE", False)
    def test_chonkie_semantic_falls_back_to_recursive(self, caplog):
        chunker = DocumentChunker(strategy=ChunkStrategy.CHONKIE_SEMANTIC)
        assert chunker._strategy == ChunkStrategy.RECURSIVE

    @patch("arrow_lake.ingest.chunker._CHONKIE_AVAILABLE", True)
    def test_semantic_falls_back_to_token_without_model(self, caplog):
        chunker = DocumentChunker(strategy=ChunkStrategy.CHONKIE_SEMANTIC, embedding_model="")
        assert chunker._strategy == ChunkStrategy.CHONKIE_TOKEN
        assert "embedding_model" in caplog.text.lower()

    @patch("arrow_lake.ingest.chunker._CHONKIE_AVAILABLE", True)
    def test_sdpm_falls_back_to_token_without_model(self, caplog):
        chunker = DocumentChunker(strategy=ChunkStrategy.CHONKIE_SDPM, embedding_model="")
        assert chunker._strategy == ChunkStrategy.CHONKIE_TOKEN


class TestChonkieIntegration:
    """Test chonkie integration (mocked via sys.modules)."""

    def test_chonkie_token_strategy(self):
        mock_chonkie = _mock_module("chonkie")
        mock_chunk = MagicMock()
        mock_chunk.text = "token chunk"
        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = [mock_chunk]
        mock_chonkie.TokenChunker.return_value = mock_chunker

        try:
            import importlib

            from arrow_lake.ingest import chunker as chunker_mod
            importlib.reload(chunker_mod)

            chunker = chunker_mod.DocumentChunker(
                strategy=ChunkStrategy.CHONKIE_TOKEN, chunk_size=256,
            )
            chunks = chunker.chunk([(1, "some text"), (2, "more text")])
            assert len(chunks) == 2
            assert chunks[0].text == "token chunk"
            assert chunks[0].page_number == 1
            assert chunks[1].page_number == 2
            mock_chonkie.TokenChunker.assert_called_once_with(
                chunk_size=256,
                chunk_overlap=64,
            )
        finally:
            del sys.modules["chonkie"]
            importlib.reload(chunker_mod)

    def test_chonkie_token_with_custom_tokenizer(self):
        mock_chonkie = _mock_module("chonkie")
        mock_chunk = MagicMock()
        mock_chunk.text = "chunk"
        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = [mock_chunk]
        mock_chonkie.TokenChunker.return_value = mock_chunker

        try:
            import importlib

            from arrow_lake.ingest import chunker as chunker_mod
            importlib.reload(chunker_mod)

            chunker = chunker_mod.DocumentChunker(
                strategy=ChunkStrategy.CHONKIE_TOKEN,
                tokenizer="bert-base-uncased",
            )
            chunker.chunk([(1, "text")])
            mock_chonkie.TokenChunker.assert_called_once_with(
                chunk_size=512,
                chunk_overlap=64,
                tokenizer="bert-base-uncased",
            )
        finally:
            del sys.modules["chonkie"]
            importlib.reload(chunker_mod)


class TestPageTracking:
    """Test that page numbers are preserved across all strategies."""

    def test_recursive_page_tracking(self):
        text = "This is sentence one. This is sentence two. " * 20
        chunker = DocumentChunker(strategy=ChunkStrategy.RECURSIVE, chunk_size=100)
        pages = [(1, text), (2, text), (3, text)]
        chunks = chunker.chunk(pages)
        for c in chunks:
            assert c.page_number in {1, 2, 3}
        assert chunks[0].page_number == 1

    def test_empty_pages_skipped(self):
        chunker = DocumentChunker(strategy=ChunkStrategy.PAGE)
        pages = [(1, "Page one"), (2, ""), (3, "Page three")]
        chunks = chunker.chunk(pages)
        assert len(chunks) == 2
        assert chunks[0].page_number == 1
        assert chunks[1].page_number == 3
        assert chunks[0].chunk_index == 0
        assert chunks[1].chunk_index == 1

    def test_empty_input(self):
        chunker = DocumentChunker(strategy=ChunkStrategy.RECURSIVE)
        assert chunker.chunk([]) == []

    def test_chunk_index_sequential(self):
        text = "Sentence one. Sentence two. " * 30
        chunker = DocumentChunker(strategy=ChunkStrategy.RECURSIVE, chunk_size=50)
        pages = [(1, text)]
        chunks = chunker.chunk(pages)
        for i, c in enumerate(chunks):
            assert c.chunk_index == i


class TestDocumentConfigNewFields:
    """Test new semantic chunking config fields."""

    def test_new_field_defaults(self):
        config = DocumentConfig()
        assert config.chunk_tokenizer == ""
        assert config.semantic_embedding_model == ""
        assert config.semantic_similarity_threshold == 0.5
        assert config.semantic_min_chunk_size == 100

    def test_semantic_threshold_validation(self):
        with pytest.raises(ValueError, match="semantic_similarity_threshold"):
            DocumentConfig(semantic_similarity_threshold=1.5)

    def test_semantic_threshold_lower_bound(self):
        with pytest.raises(ValueError, match="semantic_similarity_threshold"):
            DocumentConfig(semantic_similarity_threshold=-0.1)

    def test_new_enum_values_accepted(self):
        config = DocumentConfig(chunk_strategy=ChunkStrategy.SEMCHUNK)
        assert config.chunk_strategy == ChunkStrategy.SEMCHUNK

        config = DocumentConfig(chunk_strategy=ChunkStrategy.CHONKIE_TOKEN)
        assert config.chunk_strategy == ChunkStrategy.CHONKIE_TOKEN

        config = DocumentConfig(chunk_strategy=ChunkStrategy.CHONKIE_SEMANTIC)
        assert config.chunk_strategy == ChunkStrategy.CHONKIE_SEMANTIC

        config = DocumentConfig(chunk_strategy=ChunkStrategy.CHONKIE_SDPM)
        assert config.chunk_strategy == ChunkStrategy.CHONKIE_SDPM
