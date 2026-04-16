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
