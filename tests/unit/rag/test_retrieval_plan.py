"""Tests for arrow_lake.rag.plan — score-column resolution + RAGQueryPlan (架构评审 #1).

Consolidates the "folk convention" score-column names and packages retrieval
intent into a plan resolved once at the seam.
"""

from __future__ import annotations

import pytest

from arrow_lake.config import RAGConfig
from arrow_lake.rag.plan import RAGQueryPlan, resolve_score_column


class TestResolveScoreColumn:
    """resolve_score_column replaces the old pipeline.py:240 hardcode."""

    def test_hybrid_prefers_rrf_score(self):
        cols = ["text_content", "row_id", "_rrf_score", "_score"]
        assert resolve_score_column("hybrid", cols) == "_rrf_score"

    def test_fts_prefers_score(self):
        cols = ["text_content", "row_id", "_score"]
        assert resolve_score_column("fts", cols) == "_score"

    def test_vector_no_score_column_returns_none(self):
        # vector path produces only _distance — no score column
        cols = ["text_content", "row_id", "_distance"]
        assert resolve_score_column("vector", cols) is None

    def test_preferred_absent_returns_none(self):
        # hybrid table missing _rrf_score (only _score) → None
        # (matches old pipeline.py:240: preferred not in columns → None)
        cols = ["text_content", "row_id", "_score"]
        assert resolve_score_column("hybrid", cols) is None


class TestRAGQueryPlanResolve:
    """RAGQueryPlan.resolve parses None → config defaults in one place."""

    def test_strategy_none_falls_back_to_config_default(self):
        config = RAGConfig(enabled=True)  # default_retrieval_strategy="hybrid"
        plan = RAGQueryPlan.resolve("q", "ds", top_k=None, strategy=None, config=config)
        assert plan.strategy == config.default_retrieval_strategy
        assert plan.top_k == config.default_top_k

    def test_explicit_strategy_and_top_k_preserved(self):
        config = RAGConfig(enabled=True)
        plan = RAGQueryPlan.resolve("q", "ds", top_k=5, strategy="vector", config=config)
        assert plan.strategy == "vector"
        assert plan.top_k == 5
        assert plan.question == "q"
        assert plan.dataset_name == "ds"

    def test_rerank_top_n_from_config(self):
        config = RAGConfig(enabled=True)  # reranker_top_n default 10
        plan = RAGQueryPlan.resolve("q", "ds", top_k=20, strategy="hybrid", config=config)
        assert plan.rerank_top_n == config.reranker_top_n

    def test_plan_is_frozen(self):
        config = RAGConfig(enabled=True)
        plan = RAGQueryPlan.resolve("q", "ds", top_k=5, strategy="fts", config=config)
        with pytest.raises(Exception):
            plan.strategy = "hybrid"  # type: ignore[misc]
