"""Tests for arrow_lake.embed.encoder — EmbeddingBatch, EmbeddingResult, LocalEmbeddingEncoder, ApiEmbeddingEncoder.

所有测试均 mock 重依赖（sentence_transformers, torch, httpx），不下载模型。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pyarrow as pa
import pytest
from arrow_lake.embed.encoder import (
    ApiEmbeddingEncoder,
    EmbeddingBatch,
    EmbeddingResult,
    LocalEmbeddingEncoder,
)
from arrow_lake.exceptions import EmbeddingError, ErrorCode


# ---------------------------------------------------------------------------
# EmbeddingBatch
# ---------------------------------------------------------------------------


class TestEmbeddingBatch:
    """EmbeddingBatch frozen dataclass."""

    def test_creation(self) -> None:
        embeddings = np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32)
        batch = EmbeddingBatch(embeddings=embeddings, null_mask=(False, False))
        assert batch.embeddings.shape == (2, 2)
        assert batch.null_mask == (False, False)

    def test_frozen_immutability(self) -> None:
        embeddings = np.zeros((1, 3), dtype=np.float32)
        batch = EmbeddingBatch(embeddings=embeddings, null_mask=(False,))
        with pytest.raises(AttributeError):
            batch.null_mask = (True,)  # type: ignore[misc]
        with pytest.raises(AttributeError):
            batch.embeddings = np.zeros((2, 3), dtype=np.float32)  # type: ignore[misc]

    def test_with_null_mask(self) -> None:
        embeddings = np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]], dtype=np.float32)
        batch = EmbeddingBatch(embeddings=embeddings, null_mask=(False, True, False))
        assert batch.null_mask == (False, True, False)
        assert batch.embeddings.shape[0] == 3


# ---------------------------------------------------------------------------
# EmbeddingResult
# ---------------------------------------------------------------------------


class TestEmbeddingResult:
    """EmbeddingResult frozen dataclass."""

    def test_creation(self) -> None:
        result = EmbeddingResult(
            total_rows=10,
            embedded_rows=8,
            null_rows=2,
            embedding_dim=768,
            vector_column="text_content_embedding",
        )
        assert result.total_rows == 10
        assert result.embedded_rows == 8
        assert result.null_rows == 2
        assert result.embedding_dim == 768
        assert result.vector_column == "text_content_embedding"

    def test_frozen_immutability(self) -> None:
        result = EmbeddingResult(
            total_rows=1, embedded_rows=1, null_rows=0, embedding_dim=3, vector_column="v"
        )
        with pytest.raises(AttributeError):
            result.total_rows = 99  # type: ignore[misc]
        with pytest.raises(AttributeError):
            result.embedding_dim = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# LocalEmbeddingEncoder
# ---------------------------------------------------------------------------


def _make_table(texts: list[str | None], column: str = "text_content") -> pa.Table:
    """构建含可能为 None 值的 Arrow 表格。"""
    return pa.table({column: texts})


class TestLocalEmbeddingEncoder:
    """LocalEmbeddingEncoder — lazy loading, GPU detection, batch encoding."""

    def test_init_default_params(self) -> None:
        enc = LocalEmbeddingEncoder()
        assert enc.model_name == "Qwen/Qwen3-Embedding-0.6B"
        assert enc.model_source == "huggingface"
        assert enc.batch_size == 128
        assert enc._expected_dim == 0
        assert enc._model is None

    def test_init_custom_params(self) -> None:
        enc = LocalEmbeddingEncoder(
            model_name="BAAI/bge-small-en",
            model_source="modelscope",
            batch_size=64,
            expected_dim=512,
        )
        assert enc.model_name == "BAAI/bge-small-en"
        assert enc.model_source == "modelscope"
        assert enc.batch_size == 64
        assert enc._expected_dim == 512

    @patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False)
    def test_encode_column_with_nulls(
        self,
        _mock_metrics_enabled: MagicMock,
    ) -> None:
        """包含 None 行的表格：仅对非 null 行做 embedding。"""
        enc = LocalEmbeddingEncoder(batch_size=4)
        dim = 8
        mock_model = MagicMock()
        mock_model.encode.return_value = np.random.rand(2, dim).astype(np.float32)
        mock_model.get_sentence_embedding_dimension.return_value = dim
        enc._model = mock_model
        enc._embedding_dim = dim

        table = _make_table(["hello", None, "world", None])
        result = enc.encode_column(table, "text_content")

        assert result.total_rows == 4
        assert result.embedded_rows == 2
        assert result.null_rows == 2
        assert result.embedding_dim == dim
        assert result.vector_column == "text_content_embedding"
        mock_model.encode.assert_called_once()
        call_args = mock_model.encode.call_args
        # encode 应仅传入非 null 文本
        assert call_args[0][0] == ["hello", "world"]
        assert call_args[1]["batch_size"] == 4

    @patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False)
    def test_encode_column_all_valid(
        self,
        _mock_metrics_enabled: MagicMock,
    ) -> None:
        """全部行有效时 null_rows 为 0。"""
        enc = LocalEmbeddingEncoder(batch_size=2)
        dim = 16
        mock_model = MagicMock()
        mock_model.encode.return_value = np.random.rand(3, dim).astype(np.float32)
        mock_model.get_sentence_embedding_dimension.return_value = dim
        enc._model = mock_model
        enc._embedding_dim = dim

        table = _make_table(["alpha", "beta", "gamma"])
        result = enc.encode_column(table, "text_content")

        assert result.total_rows == 3
        assert result.embedded_rows == 3
        assert result.null_rows == 0
        assert result.embedding_dim == dim

    @patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False)
    def test_encode_column_empty_table(
        self,
        _mock_metrics_enabled: MagicMock,
    ) -> None:
        """空表直接返回零结果，不调用模型。"""
        enc = LocalEmbeddingEncoder()
        table = _make_table([])

        result = enc.encode_column(table, "text_content")
        assert result.total_rows == 0
        assert result.embedded_rows == 0
        assert result.null_rows == 0
        assert result.embedding_dim == 0
        assert result.vector_column == "text_content_embedding"

    @patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False)
    def test_encode_column_all_null(
        self,
        _mock_metrics_enabled: MagicMock,
    ) -> None:
        """全部为 null 时返回空 embedding 结果。"""
        enc = LocalEmbeddingEncoder()
        table = _make_table([None, None, None])

        result = enc.encode_column(table, "text_content")
        assert result.total_rows == 3
        assert result.embedded_rows == 0
        assert result.null_rows == 3
        assert result.embedding_dim == 0

    @patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False)
    def test_encode_column_respects_batch_size(
        self,
        _mock_metrics_enabled: MagicMock,
    ) -> None:
        """验证 batch_size 被传递给 model.encode。"""
        enc = LocalEmbeddingEncoder(batch_size=2)
        dim = 4
        mock_model = MagicMock()
        mock_model.encode.return_value = np.random.rand(3, dim).astype(np.float32)
        mock_model.get_sentence_embedding_dimension.return_value = dim
        enc._model = mock_model
        enc._embedding_dim = dim

        table = _make_table(["a", "b", "c"])
        enc.encode_column(table, "text_content")

        call_kwargs = mock_model.encode.call_args[1]
        assert call_kwargs["batch_size"] == 2

    def test_encode_column_missing_column_raises(self) -> None:
        """列不存在时抛出 ValueError。"""
        enc = LocalEmbeddingEncoder()
        table = _make_table(["hello"])
        with pytest.raises(ValueError, match="not found"):
            enc.encode_column(table, "nonexistent")

    @patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=False)
    def test_encode_column_model_runtime_error(
        self,
        _mock_metrics_enabled: MagicMock,
    ) -> None:
        """模型编码时抛 RuntimeError 应被包装为 EmbeddingError。"""
        enc = LocalEmbeddingEncoder()
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 8
        enc._model = mock_model
        enc._embedding_dim = 8
        mock_model.encode.side_effect = RuntimeError("CUDA OOM")

        table = _make_table(["text"])
        with pytest.raises(EmbeddingError) as exc_info:
            enc.encode_column(table, "text_content")
        assert exc_info.value.error_code == ErrorCode.EMBEDDING_MODEL_ERROR

    @patch("arrow_lake.core.metrics.processing_embeddings_total")
    @patch("arrow_lake.core.metrics.get_metrics_enabled", return_value=True)
    def test_encode_column_metrics_recorded(
        self,
        _mock_metrics_enabled: MagicMock,
        mock_metrics_counter: MagicMock,
    ) -> None:
        """metrics 启用时记录嵌入行数。"""
        mock_labels = MagicMock()
        mock_metrics_counter.labels.return_value = mock_labels

        enc = LocalEmbeddingEncoder(batch_size=2)
        dim = 4
        mock_model = MagicMock()
        mock_model.encode.return_value = np.random.rand(2, dim).astype(np.float32)
        mock_model.get_sentence_embedding_dimension.return_value = dim
        enc._model = mock_model
        enc._embedding_dim = dim

        table = _make_table(["x", "y"])
        enc.encode_column(table, "text_content")

        mock_metrics_counter.labels.assert_called_with(model=enc.model_name)
        mock_labels.inc.assert_called_with(2)

    def test_load_model_gpu_detection(self) -> None:
        """torch.cuda.is_available() 为 True 时应传递 device='cuda'。"""
        enc = LocalEmbeddingEncoder()
        mock_st_cls = MagicMock()
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 128
        mock_st_cls.return_value = mock_model

        with (
            patch("torch.cuda.is_available", return_value=True),
            patch("sentence_transformers.SentenceTransformer", mock_st_cls),
        ):
            loaded = enc._load_model()

        assert loaded is mock_model
        mock_st_cls.assert_called_once()
        _, kwargs = mock_st_cls.call_args
        assert kwargs["device"] == "cuda"

    def test_load_model_no_gpu(self) -> None:
        """torch 不可用时 device 应为 None。"""
        enc = LocalEmbeddingEncoder()
        mock_st_cls = MagicMock()
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 64
        mock_st_cls.return_value = mock_model

        with (
            patch("torch.cuda.is_available", return_value=False),
            patch("sentence_transformers.SentenceTransformer", mock_st_cls),
        ):
            loaded = enc._load_model()

        assert loaded is mock_model
        _, kwargs = mock_st_cls.call_args
        assert kwargs["device"] is None

    def test_load_model_torch_import_error(self) -> None:
        """torch 导入失败时静默回退，device=None。"""
        enc = LocalEmbeddingEncoder()
        mock_st_cls = MagicMock()
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 64
        mock_st_cls.return_value = mock_model

        import builtins

        original_import = builtins.__import__

        def _stub_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "torch":
                raise ImportError("no torch")
            return original_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=_stub_import),
            patch("sentence_transformers.SentenceTransformer", mock_st_cls),
        ):
            loaded = enc._load_model()

        assert loaded is mock_model
        _, kwargs = mock_st_cls.call_args
        assert kwargs["device"] is None

    def test_load_model_dimension_mismatch_raises(self) -> None:
        """expected_dim 与模型实际维度不一致时抛 EmbeddingError。"""
        enc = LocalEmbeddingEncoder(expected_dim=512)
        mock_st_cls = MagicMock()
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 128
        mock_st_cls.return_value = mock_model

        with (
            patch("torch.cuda.is_available", return_value=False),
            patch("sentence_transformers.SentenceTransformer", mock_st_cls),
            pytest.raises(EmbeddingError) as exc_info,
        ):
            enc._load_model()

        assert exc_info.value.error_code == ErrorCode.EMBEDDING_MODEL_ERROR
        assert "dimension mismatch" in exc_info.value.message.lower()

    def test_load_model_no_dimension_method_raises(self) -> None:
        """模型缺少维度内省方法时抛 EmbeddingError。"""
        enc = LocalEmbeddingEncoder()
        mock_st_cls = MagicMock()
        mock_model = MagicMock()
        # 移除两个可能的维度方法
        del mock_model.get_sentence_embedding_dimension
        del mock_model.get_embedding_dimension
        mock_st_cls.return_value = mock_model

        with (
            patch("torch.cuda.is_available", return_value=False),
            patch("sentence_transformers.SentenceTransformer", mock_st_cls),
            pytest.raises(EmbeddingError) as exc_info,
        ):
            enc._load_model()

        assert exc_info.value.error_code == ErrorCode.EMBEDDING_MODEL_ERROR


# ---------------------------------------------------------------------------
# ApiEmbeddingEncoder
# ---------------------------------------------------------------------------


class TestApiEmbeddingEncoder:
    """ApiEmbeddingEncoder — retry, fallback, error mapping."""

    def test_init_default_params(self) -> None:
        with patch("arrow_lake.embed.encoder.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            enc = ApiEmbeddingEncoder(api_base="https://api.example.com/v1")

        assert enc.api_base == "https://api.example.com/v1"
        assert enc.model_name == "text-embedding-ada-002"
        assert enc.batch_size == 128
        assert enc.timeout_seconds == 30.0
        assert enc.max_retries == 3
        assert enc.fallback_model == "Qwen/Qwen3-Embedding-0.6B"

    def test_init_api_base_required(self) -> None:
        with pytest.raises(ValueError, match="api_base is required"):
            ApiEmbeddingEncoder(api_base="")

    def test_init_strips_trailing_slash(self) -> None:
        with patch("arrow_lake.embed.encoder.httpx.Client"):
            enc = ApiEmbeddingEncoder(api_base="https://api.example.com/v1/")
        assert enc.api_base == "https://api.example.com/v1"

    def test_init_sets_auth_header_with_key(self) -> None:
        with patch("arrow_lake.embed.encoder.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            ApiEmbeddingEncoder(api_base="https://api.example.com", api_key="sk-123")

        call_kwargs = mock_client_cls.call_args[1]
        headers = call_kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-123"

    @patch("arrow_lake.embed.encoder.httpx.Client")
    def test_encode_success(self, mock_client_cls: MagicMock) -> None:
        """正常调用 API 成功返回 EmbeddingBatch。"""
        dim = 4
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]},
                {"index": 1, "embedding": [0.5, 0.6, 0.7, 0.8]},
            ],
        }
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        enc = ApiEmbeddingEncoder(api_base="https://api.example.com")
        result = enc.encode(["hello", "world"])

        assert isinstance(result, EmbeddingBatch)
        assert result.embeddings.shape == (2, dim)
        assert result.null_mask == (False, False)
        mock_client.post.assert_called_once()

    @patch("arrow_lake.embed.encoder.httpx.Client")
    def test_encode_429_raises_immediately(self, mock_client_cls: MagicMock) -> None:
        """429 状态码应直接抛 EmbeddingError（不被 retry 捕获）。"""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limited"

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        enc = ApiEmbeddingEncoder(api_base="https://api.example.com", max_retries=2)
        with pytest.raises(EmbeddingError) as exc_info:
            enc.encode(["test"])

        assert exc_info.value.error_code == ErrorCode.EMBEDDING_API_ERROR
        assert "Rate limited" in exc_info.value.message

    @patch("arrow_lake.embed.encoder.httpx.Client")
    def test_encode_non_200_raises(self, mock_client_cls: MagicMock) -> None:
        """非 200 非 429 状态码应抛 EmbeddingError。"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        enc = ApiEmbeddingEncoder(api_base="https://api.example.com")
        with pytest.raises(EmbeddingError) as exc_info:
            enc.encode(["test"])

        assert exc_info.value.error_code == ErrorCode.EMBEDDING_API_ERROR
        assert "500" in exc_info.value.message

    @patch("arrow_lake.embed.encoder.httpx.Client")
    def test_encode_timeout_raises(self, mock_client_cls: MagicMock) -> None:
        """超时应抛 EMBEDDING_TIMEOUT 错误。"""
        import httpx

        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.TimeoutException("timed out")
        mock_client_cls.return_value = mock_client

        enc = ApiEmbeddingEncoder(
            api_base="https://api.example.com", max_retries=1
        )
        with pytest.raises(EmbeddingError) as exc_info:
            enc.encode(["test"])

        assert exc_info.value.error_code == ErrorCode.EMBEDDING_TIMEOUT

    @patch("arrow_lake.embed.encoder.httpx.Client")
    def test_encode_fallback_on_connect_error(
        self, mock_client_cls: MagicMock
    ) -> None:
        """连接错误时应回退到本地编码器（_fallback_encode）。"""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        enc = ApiEmbeddingEncoder(api_base="https://api.example.com", max_retries=1)

        dim = 4
        mock_local_model = MagicMock()
        mock_local_model.encode.return_value = np.random.rand(2, dim).astype(np.float32)
        mock_local_model.get_sentence_embedding_dimension.return_value = dim

        with patch("sentence_transformers.SentenceTransformer", return_value=mock_local_model):
            result = enc._fallback_encode(["a", "b"])

        assert isinstance(result, EmbeddingBatch)
        assert result.embeddings.shape[0] == 2
        assert result.null_mask == (False, False)

    def test_fallback_encode_import_error_raises(self) -> None:
        """本地回退导入失败时应抛 EmbeddingError。"""
        ApiEmbeddingEncoder._fallback_cache.clear()
        with patch("arrow_lake.embed.encoder.httpx.Client"):
            enc = ApiEmbeddingEncoder(api_base="https://api.example.com")

        with patch("builtins.__import__", side_effect=ImportError("no sentence_transformers")):
            with pytest.raises(EmbeddingError) as exc_info:
                enc._fallback_encode(["test"])

        assert exc_info.value.error_code == ErrorCode.EMBEDDING_MODEL_ERROR
        assert "fallback" in exc_info.value.message.lower()

    @patch("arrow_lake.embed.encoder.httpx.Client")
    def test_encode_empty_list(self, mock_client_cls: MagicMock) -> None:
        """空文本列表应正常返回空 EmbeddingBatch。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        enc = ApiEmbeddingEncoder(api_base="https://api.example.com")
        result = enc.encode([])

        assert isinstance(result, EmbeddingBatch)
        # 空列表经 np.array 转换后为 1D 形状 (0,)
        assert result.embeddings.shape[0] == 0
        assert result.null_mask == ()

    @patch("arrow_lake.embed.encoder.httpx.Client")
    def test_encode_maintains_input_order(self, mock_client_cls: MagicMock) -> None:
        """API 返回乱序 data 时应按 index 排序保持原始顺序。"""
        dim = 2
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"index": 2, "embedding": [3.0, 4.0]},
                {"index": 0, "embedding": [1.0, 2.0]},
                {"index": 1, "embedding": [5.0, 6.0]},
            ],
        }
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        enc = ApiEmbeddingEncoder(api_base="https://api.example.com")
        result = enc.encode(["first", "second", "third"])

        expected = np.array([[1.0, 2.0], [5.0, 6.0], [3.0, 4.0]], dtype=np.float32)
        np.testing.assert_array_equal(result.embeddings, expected)

    @patch("arrow_lake.embed.encoder.httpx.Client")
    def test_encode_sends_correct_payload(self, mock_client_cls: MagicMock) -> None:
        """验证发送给 API 的 JSON payload 结构。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"index": 0, "embedding": [0.1, 0.2]}],
        }
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        enc = ApiEmbeddingEncoder(
            api_base="https://api.example.com",
            model_name="custom-model",
            api_key="sk-secret",
        )
        enc.encode(["hello"])

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://api.example.com/embeddings"
        payload = call_args[1]["json"]
        assert payload["model"] == "custom-model"
        assert payload["input"] == ["hello"]
