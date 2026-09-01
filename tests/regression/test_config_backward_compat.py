"""Regression test — verify config backward compatibility.

M0a Day 4 — ensures ArrowLakeConfig defaults don't silently change.
"""

from __future__ import annotations


class TestConfigBackwardCompat:
    """Verify ArrowLakeConfig() no-arg defaults remain stable."""

    def test_arrow_lake_config_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ArrowLakeConfig() with no arguments should produce expected defaults."""
        from arrow_lake.config import (
            ArrowLakeConfig,
            StorageBackend,
        )

        # The suite pins ARROW_LAKE__STORAGE__BACKEND=local and BASE_URI=<hermetic
        # tmp dir> (tests/conftest.py hermetic anchors); scrub both to assert
        # the true class defaults.
        monkeypatch.delenv("ARROW_LAKE__STORAGE__BACKEND", raising=False)
        monkeypatch.delenv("ARROW_LAKE__STORAGE__BASE_URI", raising=False)
        config = ArrowLakeConfig()

        # Storage
        assert config.storage.backend == StorageBackend.MINIO
        assert config.storage.base_uri == "./data"
        assert config.storage.s3_bucket == "arrow-lake"
        assert config.storage.s3_region == "us-east-1"

        # OLAP
        assert config.olap.max_result_rows == 100_000
        assert config.olap.enable_join is True
        assert config.olap.enable_streaming is True

        # Observability
        assert config.observability.log_level == "INFO"
        assert config.observability.metrics_enabled is True

        # Vector search
        assert config.vector.default_top_k == 10
        assert config.vector.metric == "cosine"

        # FTS
        assert config.fts.default_top_k == 10
        assert config.fts.fts_column == "text_content"

        # Hybrid
        assert config.hybrid.default_top_k == 10
        assert config.hybrid.rrf_k == 60

    def test_sub_configs_independently_constructible(self) -> None:
        """Each sub-config should be constructible with no arguments."""
        from arrow_lake.config import (
            ComputeConfig,
            FullTextSearchConfig,
            HybridSearchConfig,
            ObservabilityConfig,
            OlapConfig,
            QualityConfig,
            RedisConfig,
            StorageConfig,
            VectorSearchConfig,
            WorkflowConfig,
        )

        configs = [
            StorageConfig(),
            ComputeConfig(),
            ObservabilityConfig(),
            VectorSearchConfig(),
            FullTextSearchConfig(),
            HybridSearchConfig(),
            OlapConfig(),
            QualityConfig(),
            RedisConfig(),
            WorkflowConfig(),
        ]
        for cfg in configs:
            assert cfg is not None

    def test_new_olap_fields_have_defaults(self) -> None:
        """New v1.0 OlapConfig fields must have sensible defaults."""
        from arrow_lake.config import OlapConfig

        olap = OlapConfig()
        # v1.9.8: 默认 pyarrow_fallback 避免 IVF_PQ native lance panic(RAG 502 根因);
        # 无向量列数据集由 OlapSearchBridge._has_vector_column per-dataset 自动加速到 auto
        assert olap.lance_scan_mode == "pyarrow_fallback"
        assert olap.max_query_memory_mb >= 1
        assert olap.max_concurrent_queries >= 1
        assert olap.query_timeout_seconds >= 1
        assert olap.ducklake_enabled is False
        assert olap.ducklake_ttl_days >= 1
        assert olap.ducklake_max_join_rows >= 1
