"""Tests for arrow_lake.embed.image_encoder — Story 4.4 Image Embedding."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pyarrow as pa
import pytest
from arrow_lake.embed.image_encoder import (
    _MODEL_DIMENSIONS,
    CLIPImageEncoder,
    ImageEmbeddingResult,
)


class TestImageEmbeddingResult:
    """Test ImageEmbeddingResult frozen dataclass."""

    def test_creation(self) -> None:
        r = ImageEmbeddingResult(
            total=10,
            embedded=8,
            null_count=1,
            failed=1,
            embedding_dim=512,
            vector_column="image_embedding",
        )
        assert r.total == 10
        assert r.embedded == 8
        assert r.null_count == 1
        assert r.failed == 1
        assert r.embedding_dim == 512
        assert r.vector_column == "image_embedding"

    def test_frozen(self) -> None:
        r = ImageEmbeddingResult(
            total=1,
            embedded=1,
            null_count=0,
            failed=0,
            embedding_dim=512,
            vector_column="v",
        )
        with pytest.raises(AttributeError):
            r.total = 5

    def test_with_empty_result(self) -> None:
        r = ImageEmbeddingResult(
            total=0,
            embedded=0,
            null_count=0,
            failed=0,
            embedding_dim=0,
            vector_column="",
        )
        assert r.total == 0


class TestModelDimensions:
    """Test _MODEL_DIMENSIONS dictionary."""

    def test_clip_vit_base_patch32(self) -> None:
        assert _MODEL_DIMENSIONS["openai/clip-vit-base-patch32"] == 512

    def test_siglip_so400m_patch14_384(self) -> None:
        assert _MODEL_DIMENSIONS["google/siglip-so400m-patch14-384"] == 384

    def test_known_models_have_positive_dims(self) -> None:
        for model, dim in _MODEL_DIMENSIONS.items():
            assert dim > 0, f"{model} has invalid dim {dim}"

    def test_unknown_model_returns_zero(self) -> None:
        assert _MODEL_DIMENSIONS.get("nonexistent/model", 0) == 0


class TestCLIPImageEncoderInit:
    """Test CLIPImageEncoder initialization."""

    def test_default_model(self) -> None:
        enc = CLIPImageEncoder()
        assert enc.model_name == "openai/clip-vit-base-patch32"

    def test_custom_model(self) -> None:
        enc = CLIPImageEncoder(model_name="google/siglip-so400m-patch14-384")
        assert enc.model_name == "google/siglip-so400m-patch14-384"

    def test_default_batch_size(self) -> None:
        enc = CLIPImageEncoder()
        assert enc.batch_size == 32

    def test_custom_batch_size(self) -> None:
        enc = CLIPImageEncoder(batch_size=16)
        assert enc.batch_size == 16

    def test_default_model_source(self) -> None:
        enc = CLIPImageEncoder()
        assert enc.model_source == "modelscope"

    def test_default_image_column(self) -> None:
        enc = CLIPImageEncoder()
        assert enc.image_column == "image"

    def test_custom_image_column(self) -> None:
        enc = CLIPImageEncoder(image_column="photo")
        assert enc.image_column == "photo"

    def test_embedding_dim_from_known_model(self) -> None:
        enc = CLIPImageEncoder(model_name="openai/clip-vit-base-patch32")
        assert enc.embedding_dim == 512

    def test_embedding_dim_unknown_model(self) -> None:
        enc = CLIPImageEncoder(model_name="unknown/model")
        assert enc.embedding_dim == 0

    def test_processor_and_model_lazy(self) -> None:
        """Processor and model should not be loaded at init."""
        enc = CLIPImageEncoder()
        assert enc._processor is None
        assert enc._model is None


class TestCLIPImageEncoderEncode:
    """Test CLIPImageEncoder.encode with mocked transformers."""

    def _make_mock_transformers(self, dim: int = 512) -> tuple[MagicMock, MagicMock]:
        """Create mock processor and model."""
        processor = MagicMock()
        model = MagicMock()

        # Mock model output with normalized embeddings
        embedding = np.random.randn(dim).astype(np.float32)
        embedding = embedding / np.linalg.norm(embedding)

        model_output = MagicMock()
        # Mock torch tensor behavior: .cpu() returns self, .numpy() returns ndarray
        tensor_mock = MagicMock()
        tensor_mock.cpu.return_value = tensor_mock
        tensor_mock.numpy.return_value = np.array([embedding])
        model_output.image_embeds = tensor_mock

        processor.return_value = {"pixel_values": np.zeros((1, 3, 224, 224))}
        model.return_value = model_output

        return processor, model

    @patch("arrow_lake.embed.image_encoder.AutoImageProcessor")
    @patch("arrow_lake.embed.image_encoder.AutoModel")
    def test_encode_single_image(self, mock_model_cls: Any, mock_proc_cls: Any) -> None:
        proc, model = self._make_mock_transformers()
        mock_proc_cls.from_pretrained.return_value = proc
        mock_model_cls.from_pretrained.return_value = model

        enc = CLIPImageEncoder(model_source="huggingface")
        # Simulate image bytes
        table = pa.table(
            {
                "image": [b"\xff\xd8\xff\xe0"],  # fake JPEG header
                "id": [1],
            }
        )
        result = enc.encode(table)

        assert result.total == 1
        assert result.embedded == 1
        assert result.null_count == 0
        assert result.failed == 0
        assert result.embedding_dim == 512

    @patch("arrow_lake.embed.image_encoder.AutoImageProcessor")
    @patch("arrow_lake.embed.image_encoder.AutoModel")
    def test_encode_null_images(self, mock_model_cls: Any, mock_proc_cls: Any) -> None:
        proc, model = self._make_mock_transformers()
        mock_proc_cls.from_pretrained.return_value = proc
        mock_model_cls.from_pretrained.return_value = model

        enc = CLIPImageEncoder(model_source="huggingface")
        table = pa.table(
            {
                "image": [None, None],
                "id": [1, 2],
            }
        )
        result = enc.encode(table)

        assert result.total == 2
        assert result.embedded == 0
        assert result.null_count == 2
        assert result.failed == 0

    @patch("arrow_lake.embed.image_encoder.AutoImageProcessor")
    @patch("arrow_lake.embed.image_encoder.AutoModel")
    def test_encode_empty_table(self, mock_model_cls: Any, mock_proc_cls: Any) -> None:
        enc = CLIPImageEncoder()
        table = pa.table({"image": [], "id": []})
        result = enc.encode(table)

        assert result.total == 0
        assert result.embedded == 0
        # Processor/model should NOT be loaded for empty table
        mock_proc_cls.from_pretrained.assert_not_called()


class TestCLIPImageEncoderEncodeText:
    """Cross-modal text encoding (CLIP/SigLIP text tower → shared space).

    encode_text is the missing half of cross-modal retrieval: encode() embeds
    images, encode_text() embeds a text query into the same space so it can
    retrieve matching images via vector.search(vector_column="image_embedding").
    """

    @staticmethod
    def _make_text_mocks(dim: int = 512, n: int = 2) -> tuple[MagicMock, MagicMock, MagicMock]:
        model = MagicMock()
        emb = np.random.randn(n, dim).astype(np.float32)
        tensor_mock = MagicMock()
        tensor_mock.cpu.return_value = tensor_mock
        tensor_mock.numpy.return_value = emb
        model.get_text_features.return_value = tensor_mock

        tokenizer = MagicMock()
        tokenizer.return_value = {"input_ids": [[1, 2]] * n}

        proc = MagicMock()
        return model, tokenizer, proc

    @patch("arrow_lake.embed.image_encoder.AutoTokenizer")
    @patch("arrow_lake.embed.image_encoder.AutoImageProcessor")
    @patch("arrow_lake.embed.image_encoder.AutoModel")
    def test_encode_text_returns_normalized_vectors(
        self, mock_model_cls: Any, mock_proc_cls: Any, mock_tok_cls: Any
    ) -> None:
        dim = 512
        model, tokenizer, proc = self._make_text_mocks(dim=dim, n=2)
        mock_model_cls.from_pretrained.return_value = model
        mock_proc_cls.from_pretrained.return_value = proc
        mock_tok_cls.from_pretrained.return_value = tokenizer

        enc = CLIPImageEncoder(model_source="huggingface")
        result = enc.encode_text(["hello world", "a cat on a sofa"])

        assert result.shape == (2, dim)
        assert result.dtype == np.float32
        # L2 normalized: each row has unit norm
        norms = np.linalg.norm(result, axis=1)
        assert np.allclose(norms, 1.0, atol=1e-5)
        model.get_text_features.assert_called_once()
        tokenizer.assert_called_once()

    @patch("arrow_lake.embed.image_encoder.AutoTokenizer")
    @patch("arrow_lake.embed.image_encoder.AutoImageProcessor")
    @patch("arrow_lake.embed.image_encoder.AutoModel")
    def test_encode_text_empty_raises(
        self, mock_model_cls: Any, mock_proc_cls: Any, mock_tok_cls: Any
    ) -> None:
        enc = CLIPImageEncoder(model_source="huggingface")
        with pytest.raises(ValueError, match="empty"):
            enc.encode_text([])
        # Model not loaded when input is empty
        mock_model_cls.from_pretrained.assert_not_called()

    @patch("arrow_lake.embed.image_encoder.AutoTokenizer")
    @patch("arrow_lake.embed.image_encoder.AutoImageProcessor")
    @patch("arrow_lake.embed.image_encoder.AutoModel")
    def test_encode_text_tokenizer_cached_across_calls(
        self, mock_model_cls: Any, mock_proc_cls: Any, mock_tok_cls: Any
    ) -> None:
        model, tokenizer, proc = self._make_text_mocks(dim=512, n=1)
        mock_model_cls.from_pretrained.return_value = model
        mock_proc_cls.from_pretrained.return_value = proc
        mock_tok_cls.from_pretrained.return_value = tokenizer

        enc = CLIPImageEncoder(model_source="huggingface")
        enc.encode_text(["a"])
        enc.encode_text(["b"])
        # Tokenizer loaded once (cached on instance), called per encode
        mock_tok_cls.from_pretrained.assert_called_once()
        assert tokenizer.call_count == 2

    @patch("arrow_lake.embed.image_encoder.AutoTokenizer")
    @patch("arrow_lake.embed.image_encoder.AutoImageProcessor")
    @patch("arrow_lake.embed.image_encoder.AutoModel")
    def test_encode_text_uses_shared_model(
        self, mock_model_cls: Any, mock_proc_cls: Any, mock_tok_cls: Any
    ) -> None:
        """encode_text reuses the same model instance loaded for encode()."""
        model, tokenizer, proc = self._make_text_mocks(dim=512, n=1)
        mock_model_cls.from_pretrained.return_value = model
        mock_proc_cls.from_pretrained.return_value = proc
        mock_tok_cls.from_pretrained.return_value = tokenizer

        enc = CLIPImageEncoder(model_source="huggingface")
        enc.encode_text(["query"])
        # Same model instance as encode() would use
        assert enc._model is model
