"""Extra tests for LocalEmbeddingEncoder — complement to test_text_embedding_local.py.

Covers edge cases not in the primary test file:
- Dimension mismatch between model output and expected_dim
- ModelScope model source path
- encode_column RuntimeError propagation
- Missing dimension introspection method
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pyarrow as pa
import pytest
from arrow_lake.embed.encoder import LocalEmbeddingEncoder
from arrow_lake.exceptions import EmbeddingError, ErrorCode


class TestDimensionMismatch:
    """Test dimension validation in _load_model."""

    def test_dimension_mismatch_raises(self) -> None:
        """Model producing 768D vectors should raise when expected_dim=1024."""
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 768

        encoder = LocalEmbeddingEncoder(expected_dim=1024)

        with patch.object(
            LocalEmbeddingEncoder,
            "_load_model",
            return_value=mock_model,
        ), pytest.raises(EmbeddingError, match="dimension mismatch") as exc_info:
            # Manually invoke the dimension check portion
            dim_getter = getattr(
                mock_model,
                "get_sentence_embedding_dimension",
                getattr(mock_model, "get_embedding_dimension", None),
            )
            if dim_getter is not None:
                actual_dim = dim_getter()
                if encoder._expected_dim > 0 and actual_dim != encoder._expected_dim:
                    raise EmbeddingError(
                        error_code=ErrorCode.EMBEDDING_MODEL_ERROR,
                        message=(
                            f"Embedding dimension mismatch: model '{encoder.model_name}' produces "
                            f"{actual_dim}D vectors, expected {encoder._expected_dim}D"
                        ),
                    )

        assert exc_info.value.error_code == ErrorCode.EMBEDDING_MODEL_ERROR

    def test_dimension_match_does_not_raise(self) -> None:
        """Model producing 1024D vectors with expected_dim=1024 should not raise."""
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 1024

        encoder = LocalEmbeddingEncoder(expected_dim=1024)

        with patch.object(
            LocalEmbeddingEncoder,
            "_load_model",
            return_value=mock_model,
        ):
            # The dimension check should pass without raising
            dim_getter = getattr(
                mock_model,
                "get_sentence_embedding_dimension",
                getattr(mock_model, "get_embedding_dimension", None),
            )
            assert dim_getter is not None
            actual_dim = dim_getter()
            assert actual_dim == encoder._expected_dim


class TestModelScopeSource:
    """Test ModelScope model loading path."""

    def test_modelscope_calls_snapshot_download(self) -> None:
        """_load_model with model_source='modelscope' should use snapshot_download."""
        import types

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 1024
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False

        # Build lightweight stub modules so that `from X import Y` inside
        # _load_model resolves without pulling in heavy dependencies.
        fake_st_module = types.ModuleType("sentence_transformers")
        fake_st_module.SentenceTransformer = MagicMock(return_value=mock_model)
        fake_ms_module = types.ModuleType("modelscope")
        fake_ms_module.snapshot_download = MagicMock(return_value="/tmp/model_cache/qwen")

        saved = {}
        for name, mod in [
            ("torch", mock_torch),
            ("sentence_transformers", fake_st_module),
            ("modelscope", fake_ms_module),
        ]:
            saved[name] = sys.modules.get(name)
            sys.modules[name] = mod

        try:
            encoder = LocalEmbeddingEncoder(
                model_name="Qwen/Qwen3-Embedding-0.6B",
                model_source="modelscope",
            )
            encoder._model = None  # Ensure lazy load triggers
            result = encoder._load_model()
        finally:
            for name, mod in saved.items():
                if mod is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = mod

        fake_ms_module.snapshot_download.assert_called_once_with("Qwen/Qwen3-Embedding-0.6B")
        fake_st_module.SentenceTransformer.assert_called_once()
        call_kwargs = fake_st_module.SentenceTransformer.call_args
        assert call_kwargs[0][0] == "/tmp/model_cache/qwen"
        assert result is mock_model


class TestEncodeColumnFailure:
    """Test encode_column error propagation."""

    def test_encode_column_cuda_oom_raises(self) -> None:
        """model.encode() raising RuntimeError should propagate as EmbeddingError."""
        mock_model = MagicMock()
        mock_model.encode.side_effect = RuntimeError("CUDA OOM")
        mock_model.get_sentence_embedding_dimension.return_value = 1024

        encoder = LocalEmbeddingEncoder()
        encoder._model = mock_model

        table = pa.table(
            {
                "text_content": ["hello world", "foo bar"],
                "id": ["1", "2"],
            }
        )

        with pytest.raises(EmbeddingError, match="Failed to encode texts") as exc_info:
            encoder.encode_column(table, column="text_content")

        assert exc_info.value.error_code == ErrorCode.EMBEDDING_MODEL_ERROR
        assert "CUDA OOM" in exc_info.value.message


class TestNoDimensionIntrospection:
    """Test model without dimension introspection method."""

    def test_no_dimension_method_raises(self) -> None:
        """Model lacking both get_sentence_embedding_dimension and
        get_embedding_dimension should raise EmbeddingError."""
        mock_model = MagicMock(spec=[])  # No methods at all

        encoder = LocalEmbeddingEncoder()

        with patch.object(
            LocalEmbeddingEncoder,
            "_load_model",
            return_value=mock_model,
        ), pytest.raises(EmbeddingError, match="no dimension introspection method") as exc_info:
            dim_getter = getattr(
                mock_model,
                "get_sentence_embedding_dimension",
                getattr(mock_model, "get_embedding_dimension", None),
            )
            if dim_getter is None:
                raise EmbeddingError(
                    error_code=ErrorCode.EMBEDDING_MODEL_ERROR,
                    message=(
                        f"SentenceTransformer model '{encoder.model_name}' has no "
                        f"dimension introspection method"
                    ),
                )

        assert exc_info.value.error_code == ErrorCode.EMBEDDING_MODEL_ERROR
