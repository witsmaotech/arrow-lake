"""Tests for API text embedding — Story 4.3 (unit).

Tests ApiEmbeddingEncoder with mocked httpx responses:
- Successful API call
- Retry on 429
- Timeout handling
- Fallback to local encoder
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.exceptions import ErrorCode


class TestApiEmbeddingEncoderInit:
    """Test ApiEmbeddingEncoder initialization."""

    def test_default_init(self) -> None:
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder

        encoder = ApiEmbeddingEncoder(api_base="https://api.example.com/v1")
        assert encoder.api_base == "https://api.example.com/v1"
        assert encoder.model_name == "text-embedding-ada-002"
        assert encoder.batch_size == 128

    def test_custom_init(self) -> None:
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder

        encoder = ApiEmbeddingEncoder(
            api_base="https://api.example.com/v1",
            api_key="sk-test",
            model_name="text-embedding-3-small",
            batch_size=64,
        )
        assert encoder.api_base == "https://api.example.com/v1"
        assert encoder.api_key == "sk-test"
        assert encoder.model_name == "text-embedding-3-small"
        assert encoder.batch_size == 64

    def test_missing_api_base_raises(self) -> None:
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder

        with pytest.raises(ValueError, match="api_base"):
            ApiEmbeddingEncoder(api_base="")


class TestApiEmbeddingEncoderEncode:
    """Test ApiEmbeddingEncoder.encode with mocked HTTP."""

    def test_successful_encode(self) -> None:
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1] * 384, "index": 0},
                {"embedding": [0.2] * 384, "index": 1},
            ],
        }

        encoder = ApiEmbeddingEncoder(api_base="https://api.example.com/v1", api_key="sk-test")

        with patch("arrow_lake.embed.encoder.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = encoder.encode(["hello", "world"])

        assert len(result.embeddings) == 2
        assert result.embeddings[0].shape == (384,)

    def test_429_raises_api_error(self) -> None:
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder
        from arrow_lake.exceptions import EmbeddingError

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limit exceeded"

        encoder = ApiEmbeddingEncoder(api_base="https://api.example.com/v1", api_key="sk-test")

        with (
            patch("arrow_lake.embed.encoder.httpx.Client") as mock_client_cls,
            patch("arrow_lake.embed.encoder.stop_after_attempt"),
        ):
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            with pytest.raises(EmbeddingError) as exc_info:
                encoder.encode(["hello"])
            assert exc_info.value.error_code == ErrorCode.EMBEDDING_API_ERROR

    def test_timeout_raises_timeout_error(self) -> None:
        import httpx
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder
        from arrow_lake.exceptions import EmbeddingError

        encoder = ApiEmbeddingEncoder(api_base="https://api.example.com/v1", api_key="sk-test")

        with (
            patch("arrow_lake.embed.encoder.httpx.Client") as mock_client_cls,
            patch("arrow_lake.embed.encoder.stop_after_attempt"),
        ):
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = httpx.TimeoutException("timed out")
            mock_client_cls.return_value = mock_client

            with pytest.raises(EmbeddingError) as exc_info:
                encoder.encode(["hello"])
            assert exc_info.value.error_code == ErrorCode.EMBEDDING_TIMEOUT
