"""Tests for arrow_lake.config — Story 1.3.

Tests the 4-layer config override system:
  1. Code defaults
  2. .env file
  3. Environment variables
  4. YAML config (highest priority)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.config import (
    ArrowLakeConfig,
    ComputeConfig,
    DaftConfig,
    DecodeConfig,
    DistanceMetric,
    EmbeddingConfig,
    FilterMode,
    FullTextSearchConfig,
    HttpConfig,
    HybridSearchConfig,
    LogLevel,
    MediaConfig,
    ObservabilityConfig,
    OlapConfig,
    QualityConfig,
    SchemaValidationMode,
    StorageBackend,
    StorageConfig,
    VectorIndexType,
    VectorSearchConfig,
    WorkflowConfig,
)


class TestStorageConfig:
    """Test StorageConfig defaults and validation."""

    def test_default_backend_is_minio(self) -> None:
        config = StorageConfig()
        assert config.backend == "minio"

    def test_default_s3_endpoint(self) -> None:
        config = StorageConfig()
        assert config.s3_endpoint == "http://localhost:9000"

    def test_default_s3_bucket(self) -> None:
        config = StorageConfig()
        assert config.s3_bucket == "arrow-lake"

    def test_default_s3_region(self) -> None:
        config = StorageConfig()
        assert config.s3_region == "us-east-1"

    def test_sensitive_fields_have_empty_defaults(self) -> None:
        config = StorageConfig()
        assert config.s3_access_key == ""
        assert config.s3_secret_key == ""

    def test_custom_values_override_defaults(self) -> None:
        config = StorageConfig(
            backend="s3",
            s3_endpoint="https://s3.amazonaws.com",
            s3_bucket="prod-lake",
        )
        assert config.backend == "s3"
        assert config.s3_endpoint == "https://s3.amazonaws.com"
        assert config.s3_bucket == "prod-lake"


class TestComputeConfig:
    """Test ComputeConfig defaults and validation."""

    def test_default_ray_address(self) -> None:
        config = ComputeConfig()
        assert config.ray_address == "auto"


class TestObservabilityConfig:
    """Test ObservabilityConfig defaults and validation."""

    def test_default_metrics_enabled(self) -> None:
        config = ObservabilityConfig()
        assert config.metrics_enabled is True

    def test_default_metrics_port(self) -> None:
        config = ObservabilityConfig()
        assert config.metrics_port == 8001  # v1.6.0: changed from 8000 to avoid API port conflict

    def test_default_log_level(self) -> None:
        config = ObservabilityConfig()
        assert config.log_level == "INFO"

    def test_default_correlation_id_is_empty(self) -> None:
        config = ObservabilityConfig()
        assert config.correlation_id == ""

    def test_metrics_port_must_be_valid(self) -> None:
        with pytest.raises(ValueError):
            ObservabilityConfig(metrics_port=0)
        with pytest.raises(ValueError):
            ObservabilityConfig(metrics_port=70000)

    def test_log_level_must_be_valid(self) -> None:
        with pytest.raises(ValueError):
            ObservabilityConfig(log_level="INVALID")


class TestArrowLakeConfig:
    """Test ArrowLakeConfig composition and defaults."""

    def test_has_sub_configs(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.storage, StorageConfig)
        assert isinstance(config.compute, ComputeConfig)
        assert isinstance(config.observability, ObservabilityConfig)

    def test_default_storage_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ARROW_LAKE__STORAGE__S3_ENDPOINT", raising=False)
        # The suite pins BACKEND=local (tests/conftest.py hermetic anchor);
        # scrub it to assert the true class default. _env_file=None also skips
        # the repo root .env (deploy values), else the assertion is env-coupled
        # (passed locally via .env's 127.0.0.1, failed on CI's class default).
        monkeypatch.delenv("ARROW_LAKE__STORAGE__BACKEND", raising=False)
        config = ArrowLakeConfig(_env_file=None)
        assert config.storage.backend == "minio"
        assert config.storage.s3_endpoint == "http://localhost:9000"

    def test_default_observability_values(self) -> None:
        config = ArrowLakeConfig()
        assert config.observability.metrics_enabled is True
        assert config.observability.metrics_port == 8001
        assert config.observability.log_level == "INFO"


class TestEnvOverride:
    """Test environment variable overrides (layer 3)."""

    def test_env_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__STORAGE__BACKEND", "s3")
        config = ArrowLakeConfig()
        assert config.storage.backend == "s3"

    def test_env_overrides_s3_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__STORAGE__S3_ENDPOINT", "https://custom.s3.com")
        config = ArrowLakeConfig()
        assert config.storage.s3_endpoint == "https://custom.s3.com"

    def test_env_overrides_metrics_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__OBSERVABILITY__METRICS_PORT", "9090")
        config = ArrowLakeConfig()
        assert config.observability.metrics_port == 9090

    def test_env_overrides_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__OBSERVABILITY__LOG_LEVEL", "DEBUG")
        config = ArrowLakeConfig()
        assert config.observability.log_level == "DEBUG"

    def test_env_overrides_correlation_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__OBSERVABILITY__CORRELATION_ID", "trace-123")
        config = ArrowLakeConfig()
        assert config.observability.correlation_id == "trace-123"

    def test_no_env_uses_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ARROW_LAKE__STORAGE__BACKEND", raising=False)
        config = ArrowLakeConfig()
        assert config.storage.backend == "minio"


class TestYamlOverride:
    """Test YAML config override (layer 4 — highest priority)."""

    def test_yaml_overrides_defaults(self, tmp_path: Any) -> None:
        yaml_content = """
storage:
  backend: s3
  s3_endpoint: "https://s3.amazonaws.com"
  s3_region: "eu-west-1"

observability:
  metrics_port: 9090
  log_level: WARNING
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.storage.backend == "s3"
        assert config.storage.s3_endpoint == "https://s3.amazonaws.com"
        assert config.storage.s3_region == "eu-west-1"
        assert config.observability.metrics_port == 9090
        assert config.observability.log_level == "WARNING"

    def test_yaml_overrides_env(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__STORAGE__BACKEND", "s3")
        yaml_content = """
storage:
  backend: gcs
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.storage.backend == "gcs"

    def test_yaml_partial_override_keeps_defaults(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The suite pins BACKEND=local (tests/conftest.py hermetic anchor);
        # scrub it so the "keeps defaults" assertion sees the true default.
        monkeypatch.delenv("ARROW_LAKE__STORAGE__BACKEND", raising=False)
        yaml_content = """
observability:
  log_level: DEBUG
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        # Overridden
        assert config.observability.log_level == "DEBUG"
        # Kept defaults
        assert config.storage.backend == "minio"

    def test_missing_yaml_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            ArrowLakeConfig.from_yaml("/nonexistent/path.yaml")

    def test_from_yaml_with_dev_config(self) -> None:
        """Verify existing dev.yaml can be loaded."""
        config = ArrowLakeConfig.from_yaml("configs/dev.yaml")
        assert config.storage.backend == "minio"
        assert config.observability.log_level == "DEBUG"

    def test_from_yaml_with_prod_config(self) -> None:
        """Verify existing prod.yaml can be loaded."""
        config = ArrowLakeConfig.from_yaml("configs/prod.yaml")
        assert config.storage.backend == "s3"
        assert config.observability.log_level == "WARNING"


class TestFailFast:
    """Test fail-fast validation on required values."""

    def test_invalid_metrics_port_raises(self) -> None:
        with pytest.raises(ValueError, match="metrics_port"):
            ArrowLakeConfig(observability=ObservabilityConfig(metrics_port=99999))

    def test_invalid_log_level_raises(self) -> None:
        with pytest.raises(ValueError, match="log_level"):
            ArrowLakeConfig(observability=ObservabilityConfig(log_level="NONEXISTENT"))


class TestEnumTypes:
    """Test that enum-typed fields accept enum values."""

    def test_backend_accepts_enum(self) -> None:
        config = StorageConfig(backend=StorageBackend.S3)
        assert config.backend == StorageBackend.S3

    def test_backend_accepts_string(self) -> None:
        config = StorageConfig(backend="s3")
        assert config.backend == StorageBackend.S3

    def test_log_level_accepts_enum(self) -> None:
        config = ObservabilityConfig(log_level=LogLevel.DEBUG)
        assert config.log_level == LogLevel.DEBUG

    def test_log_level_accepts_string(self) -> None:
        config = ObservabilityConfig(log_level="DEBUG")
        assert config.log_level == LogLevel.DEBUG


class TestEnvFileLoading:
    """Test .env file loading (layer 2)."""

    def test_env_file_overrides_defaults(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The suite pins BACKEND=local (tests/conftest.py hermetic anchor) and
        # env vars outrank dotenv — scrub it so this dotenv-layer test sees
        # only its own .env file.
        monkeypatch.delenv("ARROW_LAKE__STORAGE__BACKEND", raising=False)
        env_content = "ARROW_LAKE__STORAGE__BACKEND=s3\n"
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        config = ArrowLakeConfig(_env_file=str(env_file))
        assert config.storage.backend == "s3"

    def test_env_file_lower_than_env_vars(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_content = "ARROW_LAKE__STORAGE__BACKEND=s3\n"
        env_file = tmp_path / ".env"
        env_file.write_text(env_content)

        monkeypatch.setenv("ARROW_LAKE__STORAGE__BACKEND", "gcs")
        config = ArrowLakeConfig(_env_file=str(env_file))
        # env var (layer 3) overrides .env (layer 2)
        assert config.storage.backend == "gcs"


class TestYamlPreservesEnv:
    """Test that YAML partial override preserves env var values."""

    def test_yaml_partial_keeps_env_override(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ARROW_LAKE__STORAGE__BACKEND", "s3")

        yaml_content = """
observability:
  log_level: DEBUG
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        # YAML override
        assert config.observability.log_level == "DEBUG"
        # Env var overrides preserved (not lost)
        assert config.storage.backend == "s3"


class TestHttpConfig:
    """Test HttpConfig defaults and validation (Story 3.2)."""

    def test_default_timeout(self) -> None:
        config = HttpConfig()
        assert config.timeout_seconds == 30.0

    def test_default_max_retries(self) -> None:
        config = HttpConfig()
        assert config.max_retries == 3

    def test_custom_values(self) -> None:
        config = HttpConfig(timeout_seconds=60.0, max_retries=5)
        assert config.timeout_seconds == 60.0
        assert config.max_retries == 5


class TestMediaConfig:
    """Test MediaConfig defaults and validation (Stories 3.3, 3.4)."""

    def test_default_thumbnail_size(self) -> None:
        config = MediaConfig()
        assert config.thumbnail_size == 64

    def test_default_preview_size(self) -> None:
        config = MediaConfig()
        assert config.preview_size == 512

    def test_default_max_image_dimension(self) -> None:
        config = MediaConfig()
        assert config.max_image_dimension == 4096


class TestEmbeddingConfig:
    """Test EmbeddingConfig defaults and validation (Stories 4.1, 4.3)."""

    def test_default_model(self) -> None:
        config = EmbeddingConfig()
        assert config.model == "Qwen/Qwen3-Embedding-0.6B"

    def test_default_batch_size(self) -> None:
        config = EmbeddingConfig()
        assert config.batch_size == 128

    def test_default_backend_is_local(self) -> None:
        config = EmbeddingConfig()
        assert config.backend == "local"

    def test_default_api_fields_empty(self) -> None:
        config = EmbeddingConfig()
        assert config.api_base == ""
        assert config.api_key == ""

    def test_custom_api_config(self) -> None:
        config = EmbeddingConfig(
            backend="openai",
            api_base="https://api.openai.com/v1",
            api_key="sk-test",
        )
        assert config.backend == "openai"
        assert config.api_base == "https://api.openai.com/v1"
        assert config.api_key == "sk-test"


class TestDecodeConfig:
    """Test DecodeConfig defaults and validation (Story 3.8)."""

    def test_default_quality_is_full(self) -> None:
        config = DecodeConfig()
        assert config.quality == "full"

    def test_custom_quality(self) -> None:
        config = DecodeConfig(quality="thumbnail")
        assert config.quality == "thumbnail"

    def test_all_quality_levels(self) -> None:
        for q in ("thumbnail", "preview", "full"):
            config = DecodeConfig(quality=q)
            assert config.quality == q


class TestNewConfigsInArrowLake:
    """Test that ArrowLakeConfig includes new Sprint 3 config sections."""

    def test_has_http_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.http, HttpConfig)

    def test_has_media_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.media, MediaConfig)

    def test_has_embedding_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.embedding, EmbeddingConfig)

    def test_has_decode_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.decode, DecodeConfig)

    def test_env_override_http_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__HTTP__TIMEOUT_SECONDS", "60.0")
        config = ArrowLakeConfig()
        assert config.http.timeout_seconds == 60.0

    def test_env_override_embedding_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__EMBEDDING__MODEL", "BAAI/bge-large-en-v1.5")
        config = ArrowLakeConfig()
        assert config.embedding.model == "BAAI/bge-large-en-v1.5"

    def test_yaml_override_new_sections(self, tmp_path: Any) -> None:
        yaml_content = """
embedding:
  model: BAAI/bge-large-en-v1.5
  batch_size: 64

media:
  thumbnail_size: 128

http:
  timeout_seconds: 10.0

decode:
  quality: thumbnail
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.embedding.model == "BAAI/bge-large-en-v1.5"
        assert config.embedding.batch_size == 64
        assert config.media.thumbnail_size == 128
        assert config.http.timeout_seconds == 10.0
        assert config.decode.quality == "thumbnail"


class TestVectorSearchConfig:
    """Test VectorSearchConfig defaults and validation (Story 5.1)."""

    def test_default_metric_is_cosine(self) -> None:
        config = VectorSearchConfig()
        assert config.metric == DistanceMetric.COSINE

    def test_default_index_type_is_ivf_pq(self) -> None:
        config = VectorSearchConfig()
        assert config.default_index_type == VectorIndexType.IVF_PQ

    def test_default_top_k(self) -> None:
        config = VectorSearchConfig()
        assert config.default_top_k == 10

    def test_default_num_partitions(self) -> None:
        config = VectorSearchConfig()
        assert config.num_partitions == 256

    def test_default_num_sub_vectors(self) -> None:
        config = VectorSearchConfig()
        assert config.num_sub_vectors == 24

    def test_default_num_bits(self) -> None:
        config = VectorSearchConfig()
        assert config.num_bits == 8

    def test_default_nprobes(self) -> None:
        config = VectorSearchConfig()
        assert config.nprobes == 20

    def test_default_max_nprobes(self) -> None:
        config = VectorSearchConfig()
        assert config.max_nprobes == 256

    def test_custom_metric(self) -> None:
        config = VectorSearchConfig(metric=DistanceMetric.L2)
        assert config.metric == DistanceMetric.L2

    def test_custom_index_type(self) -> None:
        config = VectorSearchConfig(default_index_type=VectorIndexType.IVF_FLAT)
        assert config.default_index_type == VectorIndexType.IVF_FLAT

    def test_top_k_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="default_top_k"):
            VectorSearchConfig(default_top_k=0)
        with pytest.raises(ValueError, match="default_top_k"):
            VectorSearchConfig(default_top_k=-1)

    def test_num_sub_vectors_must_be_multiple_of_8(self) -> None:
        with pytest.raises(ValueError, match="num_sub_vectors"):
            VectorSearchConfig(num_sub_vectors=7)
        with pytest.raises(ValueError, match="num_sub_vectors"):
            VectorSearchConfig(num_sub_vectors=0)

    def test_all_distance_metrics(self) -> None:
        for m in DistanceMetric:
            config = VectorSearchConfig(metric=m)
            assert config.metric == m

    def test_all_index_types(self) -> None:
        for t in VectorIndexType:
            config = VectorSearchConfig(default_index_type=t)
            assert config.default_index_type == t


class TestVectorConfigInArrowLake:
    """Test VectorSearchConfig integration in ArrowLakeConfig (Story 5.1)."""

    def test_has_vector_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.vector, VectorSearchConfig)

    def test_vector_defaults(self) -> None:
        config = ArrowLakeConfig()
        assert config.vector.metric == "cosine"
        assert config.vector.default_top_k == 10
        assert config.vector.num_sub_vectors == 24

    def test_env_override_vector_metric(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__VECTOR__METRIC", "l2")
        config = ArrowLakeConfig()
        assert config.vector.metric == "l2"

    def test_env_override_vector_nprobes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__VECTOR__NPROBES", "64")
        config = ArrowLakeConfig()
        assert config.vector.nprobes == 64

    def test_yaml_override_vector(self, tmp_path: Any) -> None:
        yaml_content = """
vector:
  metric: l2
  default_top_k: 20
  nprobes: 128
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.vector.metric == "l2"
        assert config.vector.default_top_k == 20
        assert config.vector.nprobes == 128


class TestFullTextSearchConfig:
    """Test FullTextSearchConfig defaults and validation (Story 5.2)."""

    def test_default_top_k(self) -> None:
        config = FullTextSearchConfig()
        assert config.default_top_k == 10

    def test_default_fts_column(self) -> None:
        config = FullTextSearchConfig()
        assert config.fts_column == "text_content"

    def test_default_stem(self) -> None:
        config = FullTextSearchConfig()
        assert config.stem is True

    def test_default_remove_stop_words(self) -> None:
        config = FullTextSearchConfig()
        assert config.remove_stop_words is True

    def test_default_lower_case(self) -> None:
        config = FullTextSearchConfig()
        assert config.lower_case is True

    def test_custom_values(self) -> None:
        config = FullTextSearchConfig(
            default_top_k=5,
            fts_column="description",
            stem=False,
            remove_stop_words=False,
            lower_case=False,
        )
        assert config.default_top_k == 5
        assert config.fts_column == "description"
        assert config.stem is False
        assert config.remove_stop_words is False
        assert config.lower_case is False

    def test_top_k_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="default_top_k"):
            FullTextSearchConfig(default_top_k=0)
        with pytest.raises(ValueError, match="default_top_k"):
            FullTextSearchConfig(default_top_k=-1)


class TestFullTextSearchConfigInArrowLake:
    """Test FullTextSearchConfig integration in ArrowLakeConfig (Story 5.2)."""

    def test_has_fts_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.fts, FullTextSearchConfig)

    def test_fts_defaults(self) -> None:
        config = ArrowLakeConfig()
        assert config.fts.default_top_k == 10
        assert config.fts.fts_column == "text_content"
        assert config.fts.stem is True
        assert config.fts.remove_stop_words is True
        assert config.fts.lower_case is True

    def test_env_override_fts_top_k(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__FTS__DEFAULT_TOP_K", "20")
        config = ArrowLakeConfig()
        assert config.fts.default_top_k == 20

    def test_env_override_fts_column(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__FTS__FTS_COLUMN", "description")
        config = ArrowLakeConfig()
        assert config.fts.fts_column == "description"

    def test_yaml_override_fts(self, tmp_path: Any) -> None:
        yaml_content = """
fts:
  default_top_k: 25
  fts_column: description
  stem: false
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.fts.default_top_k == 25
        assert config.fts.fts_column == "description"
        assert config.fts.stem is False


class TestHybridSearchConfig:
    """Test HybridSearchConfig defaults and validation (Story 5.3)."""

    def test_default_top_k(self) -> None:
        config = HybridSearchConfig()
        assert config.default_top_k == 10

    def test_default_rrf_k(self) -> None:
        config = HybridSearchConfig()
        assert config.rrf_k == 60

    def test_default_vector_top_k_multiplier(self) -> None:
        config = HybridSearchConfig()
        assert config.vector_top_k_multiplier == 3

    def test_default_fts_top_k_multiplier(self) -> None:
        config = HybridSearchConfig()
        assert config.fts_top_k_multiplier == 3

    def test_custom_values(self) -> None:
        config = HybridSearchConfig(
            default_top_k=5,
            rrf_k=100,
            vector_top_k_multiplier=5,
            fts_top_k_multiplier=4,
        )
        assert config.default_top_k == 5
        assert config.rrf_k == 100
        assert config.vector_top_k_multiplier == 5
        assert config.fts_top_k_multiplier == 4

    def test_positive_validation(self) -> None:
        with pytest.raises(ValueError):
            HybridSearchConfig(default_top_k=0)
        with pytest.raises(ValueError):
            HybridSearchConfig(rrf_k=0)
        with pytest.raises(ValueError):
            HybridSearchConfig(vector_top_k_multiplier=-1)
        with pytest.raises(ValueError):
            HybridSearchConfig(fts_top_k_multiplier=0)


class TestHybridSearchConfigInArrowLake:
    """Test HybridSearchConfig integration in ArrowLakeConfig (Story 5.3)."""

    def test_has_hybrid_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.hybrid, HybridSearchConfig)

    def test_hybrid_defaults(self) -> None:
        config = ArrowLakeConfig()
        assert config.hybrid.default_top_k == 10
        assert config.hybrid.rrf_k == 60
        assert config.hybrid.vector_top_k_multiplier == 3
        assert config.hybrid.fts_top_k_multiplier == 3

    def test_env_override_hybrid_top_k(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__HYBRID__DEFAULT_TOP_K", "20")
        config = ArrowLakeConfig()
        assert config.hybrid.default_top_k == 20

    def test_env_override_hybrid_rrf_k(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__HYBRID__RRF_K", "100")
        config = ArrowLakeConfig()
        assert config.hybrid.rrf_k == 100

    def test_yaml_override_hybrid(self, tmp_path: Any) -> None:
        yaml_content = """
hybrid:
  default_top_k: 15
  rrf_k: 50
  vector_top_k_multiplier: 5
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.hybrid.default_top_k == 15
        assert config.hybrid.rrf_k == 50
        assert config.hybrid.vector_top_k_multiplier == 5


class TestOlapConfig:
    """Test OlapConfig defaults and validation (Story 5.4)."""

    def test_default_max_result_rows(self) -> None:
        config = OlapConfig()
        assert config.max_result_rows == 100_000

    def test_custom_values(self) -> None:
        config = OlapConfig(
            max_result_rows=50_000,
        )
        assert config.max_result_rows == 50_000

    def test_max_result_rows_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_result_rows"):
            OlapConfig(max_result_rows=0)
        with pytest.raises(ValueError, match="max_result_rows"):
            OlapConfig(max_result_rows=-1)


class TestOlapConfigInArrowLake:
    """Test OlapConfig integration in ArrowLakeConfig (Story 5.4)."""

    def test_has_olap_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.olap, OlapConfig)

    def test_olap_defaults(self) -> None:
        config = ArrowLakeConfig()
        assert config.olap.max_result_rows == 100_000

    def test_env_override_olap_max_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__OLAP__MAX_RESULT_ROWS", "50000")
        config = ArrowLakeConfig()
        assert config.olap.max_result_rows == 50_000

    def test_yaml_override_olap(self, tmp_path: Any) -> None:
        yaml_content = """
olap:
  max_result_rows: 200000
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.olap.max_result_rows == 200_000


class TestQualityConfig:
    """Test QualityConfig defaults and validation (Epic 4)."""

    def test_default_enabled(self) -> None:
        config = QualityConfig()
        assert config.enabled is True

    def test_default_filter_mode_is_all(self) -> None:
        config = QualityConfig()
        assert config.filter_mode == FilterMode.ALL

    def test_default_active_filters_empty(self) -> None:
        config = QualityConfig()
        assert config.active_filters == ""

    def test_default_schema_validation_is_lenient(self) -> None:
        config = QualityConfig()
        assert config.schema_validation == SchemaValidationMode.LENIENT

    def test_default_text_min_chars(self) -> None:
        config = QualityConfig()
        assert config.text_min_chars == 1

    def test_default_text_max_chars_is_none(self) -> None:
        config = QualityConfig()
        assert config.text_max_chars is None

    def test_default_image_min_dimensions(self) -> None:
        config = QualityConfig()
        assert config.image_min_width == 64
        assert config.image_min_height == 64

    def test_custom_values(self) -> None:
        config = QualityConfig(
            enabled=False,
            filter_mode=FilterMode.ANY,
            active_filters="text_length,image_resolution",
            schema_validation=SchemaValidationMode.STRICT,
            text_min_chars=5,
            text_max_chars=10000,
            image_min_width=128,
            image_min_height=128,
        )
        assert config.enabled is False
        assert config.filter_mode == FilterMode.ANY
        assert config.active_filters == "text_length,image_resolution"
        assert config.schema_validation == SchemaValidationMode.STRICT
        assert config.text_min_chars == 5
        assert config.text_max_chars == 10000
        assert config.image_min_width == 128
        assert config.image_min_height == 128

    def test_filter_mode_accepts_string(self) -> None:
        config = QualityConfig(filter_mode="any")
        assert config.filter_mode == FilterMode.ANY

    def test_schema_validation_accepts_string(self) -> None:
        config = QualityConfig(schema_validation="strict")
        assert config.schema_validation == SchemaValidationMode.STRICT

    def test_all_filter_modes(self) -> None:
        for mode in FilterMode:
            config = QualityConfig(filter_mode=mode)
            assert config.filter_mode == mode

    def test_all_schema_validation_modes(self) -> None:
        for mode in SchemaValidationMode:
            config = QualityConfig(schema_validation=mode)
            assert config.schema_validation == mode


class TestQualityConfigInArrowLake:
    """Test QualityConfig integration in ArrowLakeConfig (Epic 4)."""

    def test_has_quality_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.quality, QualityConfig)

    def test_quality_defaults(self) -> None:
        config = ArrowLakeConfig()
        assert config.quality.enabled is True
        assert config.quality.filter_mode == "all"
        assert config.quality.schema_validation == "lenient"

    def test_env_override_quality_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__QUALITY__ENABLED", "false")
        config = ArrowLakeConfig()
        assert config.quality.enabled is False

    def test_env_override_quality_filter_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__QUALITY__FILTER_MODE", "any")
        config = ArrowLakeConfig()
        assert config.quality.filter_mode == "any"

    def test_env_override_quality_schema_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__QUALITY__SCHEMA_VALIDATION", "strict")
        config = ArrowLakeConfig()
        assert config.quality.schema_validation == "strict"

    def test_env_override_quality_text_min_chars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__QUALITY__TEXT_MIN_CHARS", "10")
        config = ArrowLakeConfig()
        assert config.quality.text_min_chars == 10

    def test_yaml_override_quality(self, tmp_path: Any) -> None:
        yaml_content = """
quality:
  enabled: false
  filter_mode: any
  schema_validation: strict
  text_min_chars: 5
  image_min_width: 128
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.quality.enabled is False
        assert config.quality.filter_mode == "any"
        assert config.quality.schema_validation == "strict"
        assert config.quality.text_min_chars == 5
        assert config.quality.image_min_width == 128

    def test_from_yaml_with_prod_config_quality(self) -> None:
        """Verify prod.yaml quality section loads correctly."""
        config = ArrowLakeConfig.from_yaml("configs/prod.yaml")
        assert config.quality.enabled is True
        assert config.quality.schema_validation == "strict"
        assert config.quality.image_min_width == 128
        assert config.quality.image_min_height == 128

    def test_from_yaml_with_dev_config_quality(self) -> None:
        """Verify dev.yaml quality section loads correctly."""
        config = ArrowLakeConfig.from_yaml("configs/dev.yaml")
        assert config.quality.enabled is True
        assert config.quality.schema_validation == "lenient"


class TestWorkflowConfig:
    """Test WorkflowConfig defaults and validation (Epic 6)."""

    def test_default_max_retry_attempts(self) -> None:
        config = WorkflowConfig()
        assert config.max_retry_attempts == 3

    def test_default_min_backoff_seconds(self) -> None:
        config = WorkflowConfig()
        assert config.min_backoff_seconds == 1.0

    def test_default_max_backoff_seconds(self) -> None:
        config = WorkflowConfig()
        assert config.max_backoff_seconds == 60.0

    def test_custom_values(self) -> None:
        config = WorkflowConfig(
            max_retry_attempts=5,
            min_backoff_seconds=2.0,
            max_backoff_seconds=120.0,
        )
        assert config.max_retry_attempts == 5
        assert config.min_backoff_seconds == 2.0
        assert config.max_backoff_seconds == 120.0

    def test_negative_max_retry_attempts_raises(self) -> None:
        with pytest.raises(ValueError, match="max_retry_attempts"):
            WorkflowConfig(max_retry_attempts=-1)

    def test_negative_backoff_raises(self) -> None:
        with pytest.raises(ValueError, match="backoff"):
            WorkflowConfig(min_backoff_seconds=-1.0)


class TestWorkflowConfigInArrowLake:
    """Test WorkflowConfig integration in ArrowLakeConfig (Epic 6)."""

    def test_has_workflow_config(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.workflow, WorkflowConfig)

    def test_workflow_defaults(self) -> None:
        config = ArrowLakeConfig()
        assert config.workflow.max_retry_attempts == 3

    def test_env_override_workflow_max_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__WORKFLOW__MAX_RETRY_ATTEMPTS", "5")
        config = ArrowLakeConfig()
        assert config.workflow.max_retry_attempts == 5

    def test_yaml_override_workflow(self, tmp_path: Any) -> None:
        yaml_content = """
workflow:
  max_retry_attempts: 5
  min_backoff_seconds: 2.0
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.workflow.max_retry_attempts == 5
        assert config.workflow.min_backoff_seconds == 2.0

    def test_from_yaml_with_dev_config_workflow(self) -> None:
        """Verify dev.yaml workflow section loads correctly."""
        config = ArrowLakeConfig.from_yaml("configs/dev.yaml")
        assert config.workflow.max_retry_attempts == 3


class TestDaftConfig:
    """Tests for DaftConfig (Story 3.7)."""

    def test_daft_defaults(self) -> None:
        config = DaftConfig()
        assert config.enabled is True

    def test_daft_in_arrowlake(self) -> None:
        config = ArrowLakeConfig()
        assert isinstance(config.daft, DaftConfig)
        assert config.daft.enabled is True

    def test_env_override_daft_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARROW_LAKE__DAFT__ENABLED", "false")
        config = ArrowLakeConfig()
        assert config.daft.enabled is False

    def test_yaml_override_daft(self, tmp_path: Any) -> None:
        yaml_content = "daft:\n  enabled: false\n"
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.daft.enabled is False


class TestDeepMergeRecursive:
    """Cover L174: result[k] = _deep_merge(result[k], v) — recursive deep merge."""

    def test_nested_dict_merge(self, tmp_path: Any) -> None:
        """YAML with nested dict keys triggers _deep_merge recursion (L174)."""
        yaml_content = """
storage:
  s3_endpoint: "https://custom.s3.com"
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        config = ArrowLakeConfig.from_yaml(str(yaml_file))
        assert config.storage.s3_endpoint == "https://custom.s3.com"


class TestYamlUnrecognizedSections:
    """Cover L197: warning log for unrecognized YAML sections."""

    def test_unrecognized_sections_logged(self, tmp_path: Any) -> None:
        """YAML with unknown sections triggers the warning at L197."""
        import logging

        yaml_content = """
observability:
  log_level: DEBUG
unknown_section:
  foo: bar
another_bad:
  baz: 123
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content)

        with patch("arrow_lake.config.main.logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            config = ArrowLakeConfig.from_yaml(str(yaml_file))

        mock_logger.warning.assert_called_once()
        msg = mock_logger.warning.call_args[0][1]
        assert "another_bad" in msg or "unknown_section" in msg
