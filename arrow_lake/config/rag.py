"""RAG and LLM configuration — LLM, RAG, HugeGraph."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from arrow_lake.config._enums import LLMProviderType


class LLMConfig(BaseModel):
    """LLM provider configuration for RAG generation.

    Attributes:
        provider: LLM backend type (openai, anthropic, vllm, ollama).
        model: Model name to use for generation.
        api_key: API key (empty for local models).
        api_base: Custom API base URL (empty = use provider default).
        temperature: Sampling temperature (0.0-2.0).
        max_tokens: Maximum tokens to generate.
        context_window_tokens: Model context window size for budget calculation.
        timeout_seconds: HTTP request timeout.
    """

    provider: LLMProviderType = LLMProviderType.OPENAI
    model: str = "gpt-4o-mini"
    api_key: str = ""
    api_base: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    context_window_tokens: int = 128000
    timeout_seconds: float = 60.0
    anthropic_version: str = "2023-06-01"

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError(f"temperature must be 0.0-2.0, got {v}")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        if v < 1.0:
            raise ValueError(f"timeout_seconds must be >= 1.0, got {v}")
        return v

    @field_validator("max_tokens")
    @classmethod
    def validate_max_tokens(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"max_tokens must be >= 1, got {v}")
        return v


class RAGConfig(BaseModel):
    """RAG pipeline configuration.

    Attributes:
        enabled: Whether RAG endpoints are active.
        default_retrieval_strategy: Default retrieval strategy (vector, fts, hybrid).
        default_top_k: Default number of results to retrieve.
        max_context_chunks: Maximum chunks in context window.
        context_budget_ratio: Fraction of context window for retrieved chunks.
        system_prompt: Default system prompt override.
        history_dataset: Lance dataset name for session history.
        enable_citations: Whether to track and return citations.
    """

    enabled: bool = False
    default_retrieval_strategy: str = "hybrid"
    default_top_k: int = 10
    max_context_chunks: int = 20
    context_budget_ratio: float = 0.75
    system_prompt: str = ""
    history_dataset: str = "_rag_sessions"
    enable_citations: bool = True
    session_ttl_seconds: int = 86400
    feedback_enabled: bool = True

    @field_validator("context_budget_ratio")
    @classmethod
    def validate_ratio(cls, v: float) -> float:
        if not 0.1 <= v <= 0.95:
            raise ValueError(f"context_budget_ratio must be 0.1-0.95, got {v}")
        return v


class HugeGraphConfig(BaseModel):
    """HugeGraph 知识图谱配置 (M3).

    Attributes:
        enabled: Whether KG functionality is active.
        host: HugeGraph server hostname.
        port: HugeGraph REST API port.
        graph_name: Name of the graph in HugeGraph.
        timeout_seconds: HTTP request timeout.
        username: Auth username (empty = no auth).
        password: Auth password.
        auto_build_on_ingest: Auto-build KG on data ingestion.
        build_batch_size: Vertices/edges per batch insert.
        default_traversal_depth: Default hop depth for graph queries.
        max_traversal_depth: Maximum allowed traversal depth.
    """

    enabled: bool = False
    host: str = "localhost"
    port: int = 8089
    graph_name: str = "arrow_lake_kg"
    timeout_seconds: float = 30.0
    username: str = ""
    password: str = ""
    auto_build_on_ingest: bool = False
    build_batch_size: int = 50
    default_traversal_depth: int = 2
    max_traversal_depth: int = 5
    vermeer_host: str = "localhost"
    vermeer_port: int = 8081

    @field_validator("max_traversal_depth")
    @classmethod
    def validate_max_depth(cls, v: int) -> int:
        if not 1 <= v <= 10:
            raise ValueError(f"max_traversal_depth must be 1-10, got {v}")
        return v

    @field_validator("build_batch_size")
    @classmethod
    def validate_batch_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"build_batch_size must be >= 1, got {v}")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        if v < 1.0:
            raise ValueError(f"timeout_seconds must be >= 1.0, got {v}")
        return v
