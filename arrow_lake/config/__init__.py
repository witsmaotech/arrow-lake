"""Arrow Lake configuration -- 4-layer override system.

ruff noqa: F821 (false positives from __future__ annotations + forward refs)

Priority (low -> high):
  1. Code defaults (Pydantic field defaults)
  2. .env file (via pydantic-settings)
  3. Environment variables (ARROW_LAKE__ prefix, via pydantic-settings)
  4. YAML config file (explicit load, highest priority)

See project-context.md Rules 3, 35-38.
"""

from arrow_lake.config._enums import (
    AuthMode,
    ChunkStrategy,
    DecodeQuality,
    DistanceMetric,
    EmbeddingBackend,
    FilterMode,
    LLMProviderType,
    LogLevel,
    ModelSource,
    OcrBackend,
    PdfParseMode,
    SchemaValidationMode,
    StorageBackend,
    VectorIndexType,
)
from arrow_lake.config.document import DocumentConfig
from arrow_lake.config.api import (
    ApiConfig,
    AuditConfig,
    AuthConfig,
    LineageConfig,
    OpenTelemetryConfig,
    RateLimitConfig,
)
from arrow_lake.config.infra import (
    ComputeConfig,
    DaftConfig,
    HttpConfig,
    LifecycleConfig,
    ObservabilityConfig,
)
from arrow_lake.config.main import ArrowLakeConfig, _build_merged_update
from arrow_lake.config.media import (
    DecodeConfig,
    EmbeddingConfig,
    ExportConfig,
    MediaConfig,
    QualityConfig,
)
from arrow_lake.config.olap import OlapConfig
from arrow_lake.config.rag import HugeGraphConfig, LLMConfig, RAGConfig
from arrow_lake.config.search import (
    EnsembleSearchConfig,
    FacetedSearchConfig,
    FullTextSearchConfig,
    HybridSearchConfig,
    VectorSearchConfig,
)
from arrow_lake.config.storage import StorageConfig
from arrow_lake.config.workflow import ArgoConfig, AutoscaleConfig, WorkflowConfig

__all__ = [
    # Top-level config
    "ArrowLakeConfig",
    # Enums
    "AuthMode",
    "ChunkStrategy",
    "DecodeQuality",
    "DistanceMetric",
    "EmbeddingBackend",
    "FilterMode",
    "LLMProviderType",
    "LogLevel",
    "ModelSource",
    "OcrBackend",
    "PdfParseMode",
    "SchemaValidationMode",
    "StorageBackend",
    "VectorIndexType",
    # Sub-configs
    "ApiConfig",
    "DocumentConfig",
    "ArgoConfig",
    "AuditConfig",
    "AuthConfig",
    "AutoscaleConfig",
    "ComputeConfig",
    "DaftConfig",
    "DecodeConfig",
    "EmbeddingConfig",
    "EnsembleSearchConfig",
    "ExportConfig",
    "FacetedSearchConfig",
    "FullTextSearchConfig",
    "HttpConfig",
    "HugeGraphConfig",
    "HybridSearchConfig",
    "LLMConfig",
    "LifecycleConfig",
    "LineageConfig",
    "MediaConfig",
    "ObservabilityConfig",
    "OlapConfig",
    "OpenTelemetryConfig",
    "QualityConfig",
    "RAGConfig",
    "RateLimitConfig",
    "StorageConfig",
    "VectorSearchConfig",
    "WorkflowConfig",
]
