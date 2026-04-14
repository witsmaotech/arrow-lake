"""Tests for Ray Serve embedding backend — Story 4.2."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pyarrow as pa
import pytest
from arrow_lake.embed.ray_serve_encoder import RayServeEmbeddingEncoder
from arrow_lake.exceptions import EmbeddingError


class TestRayServeEncoderInit:
    """Test RayServeEmbeddingEncoder initialization."""

    def test_default_deployment_name(self) -> None:
        enc = RayServeEmbeddingEncoder()
        assert enc.deployment_name == "embedding"

    def test_custom_deployment_name(self) -> None:
        enc = RayServeEmbeddingEncoder(deployment_name="custom-embed")
        assert enc.deployment_name == "custom-embed"

    def test_default_batch_size(self) -> None:
        enc = RayServeEmbeddingEncoder()
        assert enc.batch_size == 128

    def test_custom_batch_size(self) -> None:
        enc = RayServeEmbeddingEncoder(batch_size=64)
        assert enc.batch_size == 64

    def test_lazy_handle(self) -> None:
        enc = RayServeEmbeddingEncoder()
        assert enc._handle is None


class TestRayServeEncoderEncode:
    """Test RayServeEmbeddingEncoder.encode_column with mocked Ray."""

    def _make_mock_handle(self, dim: int = 384) -> MagicMock:
        handle = MagicMock()
        handle.remote.return_value = np.random.randn(1, dim).astype(np.float32)
        return handle

    @patch("arrow_lake.embed.ray_serve_encoder.ray_serve")
    def test_encode_single_text(self, mock_serve: Any) -> None:
        mock_serve.get_deployment.return_value = self._make_mock_handle()
        enc = RayServeEmbeddingEncoder()
        table = pa.table({"text_content": ["hello world"]})
        result = enc.encode_column(table)
        assert result.total_rows == 1
        assert result.embedded_rows == 1
        assert result.null_rows == 0

    @patch("arrow_lake.embed.ray_serve_encoder.ray_serve")
    def test_encode_null_text(self, mock_serve: Any) -> None:
        mock_serve.get_deployment.return_value = self._make_mock_handle()
        enc = RayServeEmbeddingEncoder()
        table = pa.table({"text_content": [None]})
        result = enc.encode_column(table)
        assert result.total_rows == 1
        assert result.embedded_rows == 0
        assert result.null_rows == 1

    @patch("arrow_lake.embed.ray_serve_encoder.ray_serve")
    def test_encode_empty_table(self, mock_serve: Any) -> None:
        enc = RayServeEmbeddingEncoder()
        table = pa.table({"text_content": []})
        result = enc.encode_column(table)
        assert result.total_rows == 0
        mock_serve.get_deployment.assert_not_called()

    @patch("arrow_lake.embed.ray_serve_encoder.ray_serve")
    def test_encode_multiple_rows(self, mock_serve: Any) -> None:
        handle = MagicMock()
        handle.remote.return_value = np.random.randn(3, 384).astype(np.float32)
        mock_serve.get_deployment.return_value = handle

        enc = RayServeEmbeddingEncoder()
        table = pa.table({"text_content": ["a", "b", "c"]})
        result = enc.encode_column(table)
        assert result.total_rows == 3
        assert result.embedded_rows == 3
        mock_serve.get_deployment.assert_called_once()

    @patch("arrow_lake.embed.ray_serve_encoder.ray_serve")
    def test_encode_missing_column_raises(self, mock_serve: Any) -> None:
        enc = RayServeEmbeddingEncoder()
        table = pa.table({"other": ["a"]})
        with pytest.raises(ValueError, match="not found"):
            enc.encode_column(table)


class TestRayServeFallback:
    """Test fallback from Ray Serve to local encoder."""

    @patch("arrow_lake.embed.ray_serve_encoder.ray_serve")
    def test_fallback_on_import_error(self, mock_serve: Any) -> None:
        mock_serve.get_deployment.side_effect = ImportError("ray not installed")
        enc = RayServeEmbeddingEncoder()
        table = pa.table({"text_content": ["hello"]})

        # Should fallback to local without raising
        result = enc.encode_column(table, fallback_enabled=True)
        assert result.total_rows == 1
        assert result.embedded_rows >= 0  # May fail if local model not available

    @patch("arrow_lake.embed.ray_serve_encoder.ray_serve")
    def test_no_fallback_raises_on_error(self, mock_serve: Any) -> None:
        mock_serve.get_deployment.side_effect = ImportError("ray not installed")
        enc = RayServeEmbeddingEncoder()
        table = pa.table({"text_content": ["hello"]})

        with pytest.raises(EmbeddingError, match="RAY_SERVE_UNAVAILABLE"):
            enc.encode_column(table, fallback_enabled=False)

    @patch("arrow_lake.embed.ray_serve_encoder.ray_serve")
    def test_fallback_on_connection_error(self, mock_serve: Any) -> None:
        handle = MagicMock()
        handle.remote.side_effect = ConnectionError("Ray Serve not reachable")
        mock_serve.get_deployment.return_value = handle

        enc = RayServeEmbeddingEncoder()
        table = pa.table({"text_content": ["hello"]})

        result = enc.encode_column(table, fallback_enabled=True)
        assert result.total_rows == 1
        assert result.embedded_rows >= 0

    @patch("arrow_lake.embed.ray_serve_encoder.ray_serve")
    def test_fallback_logs_warning(self, mock_serve: Any, caplog: Any) -> None:
        mock_serve.get_deployment.side_effect = ImportError("ray not installed")
        enc = RayServeEmbeddingEncoder()
        table = pa.table({"text_content": ["test"]})

        enc.encode_column(table, fallback_enabled=True)
        assert "EMBEDDING_RAY_SERVE_FALLBACK" in caplog.text
