"""Tests for LLMConfig and RAGConfig — M2 Day 1."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from arrow_lake.config import ArrowLakeConfig, LLMConfig, LLMProviderType, RAGConfig
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# LLMProviderType
# ---------------------------------------------------------------------------


class TestLLMProviderType:
    def test_values(self) -> None:
        assert LLMProviderType.OPENAI == "openai"
        assert LLMProviderType.ANTHROPIC == "anthropic"
        assert LLMProviderType.VLLM == "vllm"
        assert LLMProviderType.OLLAMA == "ollama"


# ---------------------------------------------------------------------------
# LLMConfig
# ---------------------------------------------------------------------------


class TestLLMConfig:
    def test_defaults(self) -> None:
        config = LLMConfig()
        assert config.provider == LLMProviderType.OPENAI
        assert config.model == "gpt-4o-mini"
        assert config.api_key == ""
        assert config.api_base == ""
        assert config.temperature == 0.7
        assert config.max_tokens == 2048
        assert config.context_window_tokens == 128000
        assert config.timeout_seconds == 60.0

    def test_custom_values(self) -> None:
        config = LLMConfig(
            provider=LLMProviderType.OLLAMA,
            model="qwen3:0.6b",
            api_base="http://localhost:11434/v1",
            temperature=0.5,
            max_tokens=4096,
        )
        assert config.provider == LLMProviderType.OLLAMA
        assert config.model == "qwen3:0.6b"
        assert config.api_base == "http://localhost:11434/v1"
        assert config.temperature == 0.5
        assert config.max_tokens == 4096

    def test_temperature_out_of_range_raises(self) -> None:
        with pytest.raises(ValidationError, match="temperature"):
            LLMConfig(temperature=3.0)

    def test_temperature_negative_raises(self) -> None:
        with pytest.raises(ValidationError, match="temperature"):
            LLMConfig(temperature=-0.1)

    def test_temperature_boundary_zero(self) -> None:
        config = LLMConfig(temperature=0.0)
        assert config.temperature == 0.0
        config = LLMConfig(temperature=2.0)
        assert config.temperature == 2.0

    def test_max_tokens_zero_raises(self) -> None:
        with pytest.raises(ValidationError, match="max_tokens"):
            LLMConfig(max_tokens=0)

    def test_max_tokens_negative_raises(self) -> None:
        with pytest.raises(ValidationError, match="max_tokens"):
            LLMConfig(max_tokens=-1)

    def test_timeout_minimum(self) -> None:
        config = LLMConfig(timeout_seconds=1.0)
        assert config.timeout_seconds == 1.0

    def test_timeout_below_minimum_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timeout_seconds"):
            LLMConfig(timeout_seconds=0.0)

    def test_timeout_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timeout_seconds"):
            LLMConfig(timeout_seconds=-1.0)


# ---------------------------------------------------------------------------
# RAGConfig
# ---------------------------------------------------------------------------


class TestRAGConfig:
    def test_defaults(self) -> None:
        config = RAGConfig()
        assert config.enabled is False
        assert config.default_retrieval_strategy == "hybrid"
        assert config.default_top_k == 10
        assert config.max_context_chunks == 20
        assert config.context_budget_ratio == 0.75
        assert config.system_prompt == ""
        assert config.history_dataset == "_rag_sessions"
        assert config.enable_citations is True

    def test_custom_values(self) -> None:
        config = RAGConfig(
            enabled=True,
            default_retrieval_strategy="vector",
            default_top_k=20,
            context_budget_ratio=0.5,
        )
        assert config.enabled is True
        assert config.default_retrieval_strategy == "vector"
        assert config.default_top_k == 20
        assert config.context_budget_ratio == 0.5

    def test_context_budget_ratio_too_low(self) -> None:
        with pytest.raises(ValidationError, match="context_budget_ratio"):
            RAGConfig(context_budget_ratio=0.05)

    def test_context_budget_ratio_too_high(self) -> None:
        with pytest.raises(ValidationError, match="context_budget_ratio"):
            RAGConfig(context_budget_ratio=1.0)

    def test_boundary_ratio(self) -> None:
        low = RAGConfig(context_budget_ratio=0.1)
        high = RAGConfig(context_budget_ratio=0.95)
        assert low.context_budget_ratio == 0.1
        assert high.context_budget_ratio == 0.95


# ---------------------------------------------------------------------------
# ArrowLakeConfig integration
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    def test_arrow_lake_config_has_llm(self) -> None:
        config = ArrowLakeConfig()
        assert hasattr(config, "llm")
        assert isinstance(config.llm, LLMConfig)

    def test_arrow_lake_config_has_rag(self) -> None:
        config = ArrowLakeConfig()
        assert hasattr(config, "rag")
        assert isinstance(config.rag, RAGConfig)

    def test_env_var_llm_provider(self) -> None:
        with patch.dict(os.environ, {"ARROW_LAKE__LLM__PROVIDER": "ollama"}, clear=True):
            config = ArrowLakeConfig()
            assert config.llm.provider == "ollama"

    def test_env_var_rag_enabled(self) -> None:
        with patch.dict(os.environ, {"ARROW_LAKE__RAG__ENABLED": "true"}, clear=True):
            config = ArrowLakeConfig()
            assert config.rag.enabled is True

    def test_llm_config_in_section_types(self) -> None:
        from arrow_lake.config import _build_merged_update

        base = ArrowLakeConfig()
        merged = _build_merged_update(base, {"llm": {"provider": "vllm"}})
        assert merged["llm"].provider == "vllm"
        # rag section absent from override — should keep base value
        assert merged["rag"].enabled is False

    def test_rag_config_in_section_types(self) -> None:
        from arrow_lake.config import _build_merged_update

        base = ArrowLakeConfig()
        merged = _build_merged_update(base, {"rag": {"enabled": True}})
        assert merged["rag"].enabled is True
