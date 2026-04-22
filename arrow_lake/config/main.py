"""Top-level ArrowLakeConfig and YAML merge logic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

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
from arrow_lake.config.media import DecodeConfig, EmbeddingConfig, ExportConfig, MediaConfig, QualityConfig
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

# Registry mapping section names to their Pydantic model classes.
# Used by both from_yaml() and _build_merged_update().
_SECTION_TYPES: dict[str, type[BaseModel]] = {
    "storage": StorageConfig,
    "compute": ComputeConfig,
    "observability": ObservabilityConfig,
    "http": HttpConfig,
    "media": MediaConfig,
    "embedding": EmbeddingConfig,
    "decode": DecodeConfig,
    "vector": VectorSearchConfig,
    "fts": FullTextSearchConfig,
    "hybrid": HybridSearchConfig,
    "olap": OlapConfig,
    "daft": DaftConfig,
    "quality": QualityConfig,
    "workflow": WorkflowConfig,
    "argo": ArgoConfig,
    "autoscale": AutoscaleConfig,
    "lifecycle": LifecycleConfig,
    "faceted": FacetedSearchConfig,
    "ensemble": EnsembleSearchConfig,
    "lineage": LineageConfig,
    "export": ExportConfig,
    "audit": AuditConfig,
    "api": ApiConfig,
    "llm": LLMConfig,
    "rag": RAGConfig,
    "hugegraph": HugeGraphConfig,
    "opentelemetry": OpenTelemetryConfig,
    "auth": AuthConfig,
    "rate_limit": RateLimitConfig,
}


class ArrowLakeConfig(BaseSettings):
    """Top-level Arrow Lake configuration.

    Loads from env vars with ARROW_LAKE__ prefix via pydantic-settings.
    Use ``from_yaml()`` for highest-priority YAML config overlay.

    Example::

        config = ArrowLakeConfig()  # loads defaults + .env + env
        config = ArrowLakeConfig.from_yaml("configs/prod.yaml")
    """

    model_config = SettingsConfigDict(
        env_prefix="ARROW_LAKE__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    storage: StorageConfig = StorageConfig()
    compute: ComputeConfig = ComputeConfig()
    observability: ObservabilityConfig = ObservabilityConfig()
    http: HttpConfig = HttpConfig()
    media: MediaConfig = MediaConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    decode: DecodeConfig = DecodeConfig()
    vector: VectorSearchConfig = VectorSearchConfig()
    fts: FullTextSearchConfig = FullTextSearchConfig()
    hybrid: HybridSearchConfig = HybridSearchConfig()
    olap: OlapConfig = OlapConfig()
    daft: DaftConfig = DaftConfig()
    quality: QualityConfig = QualityConfig()
    workflow: WorkflowConfig = WorkflowConfig()
    argo: ArgoConfig = ArgoConfig()
    autoscale: AutoscaleConfig = AutoscaleConfig()
    lifecycle: LifecycleConfig = LifecycleConfig()
    faceted: FacetedSearchConfig = FacetedSearchConfig()
    ensemble: EnsembleSearchConfig = EnsembleSearchConfig()
    lineage: LineageConfig = LineageConfig()
    audit: AuditConfig = AuditConfig()
    export: ExportConfig = ExportConfig()
    api: ApiConfig = ApiConfig()
    llm: LLMConfig = LLMConfig()
    rag: RAGConfig = RAGConfig()
    hugegraph: HugeGraphConfig = HugeGraphConfig()
    opentelemetry: OpenTelemetryConfig = OpenTelemetryConfig()
    auth: AuthConfig = AuthConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()

    @classmethod
    def from_yaml(cls, path: str | Path) -> ArrowLakeConfig:
        """Load config with YAML overlay (highest priority layer).

        Constructs the base config first (defaults + .env + env vars),
        then overlays YAML values on top.

        Args:
            path: Path to a YAML config file.

        Returns:
            ArrowLakeConfig with YAML values merged over defaults + .env + env.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Config file not found: {file_path}")

        with open(file_path) as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        base = cls()
        merged = _build_merged_update(base, raw)
        return cls(**merged)


def _build_merged_update(base: ArrowLakeConfig, yaml_data: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge YAML values onto base config sections.

    For each section present in the YAML, merge its keys into
    the corresponding base section. Sections absent from YAML
    keep their base (env-inherited) values entirely.
    """
    result: dict[str, Any] = {}

    for section, model_cls in _SECTION_TYPES.items():
        base_dict = getattr(base, section).model_dump()
        if section in yaml_data:
            base_dict.update(yaml_data[section])
        result[section] = model_cls(**base_dict)

    return result
