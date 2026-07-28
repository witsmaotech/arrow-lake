"""RAG retrieval plan + score-column resolution — single source of truth.

Consolidates the "folk convention" score-column names (``_rrf_score`` for hybrid,
``_score`` for fts) that were previously hardcoded across pipeline.py / ensemble.py /
hybrid.py / fts.py, and packages the retrieval intent (question / dataset / top_k /
strategy) into an explicit :class:`RAGQueryPlan` so strategy is resolved ONCE at the
seam instead of being smuggled as a positional arg through 5 hops. (架构评审 #1)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arrow_lake.config import RAGConfig

# Score columns produced by the retrieval backends:
# - hybrid (RRF fusion): query/hybrid.py writes ``_rrf_score``
# - fts (LanceDB native full-text): ``_score``
# - vector: NO score column (relies on the reranker; table_to_chunks defaults to 1.0)
# Consumers pick via resolve_score_column() rather than hardcoding the literal.


def resolve_score_column(strategy: str, available_columns: list[str]) -> str | None:
    """Pick the score column for a retrieval result table.

    hybrid → ``_rrf_score``, otherwise → ``_score``; if the preferred column is
    absent (e.g. the vector path produces only ``_distance``), return None so
    ``table_to_chunks`` falls back to the default score. Behavior matches the old
    pipeline.py:240 hardcode.
    """
    preferred = "_rrf_score" if strategy == "hybrid" else "_score"
    return preferred if preferred in available_columns else None


@dataclass(frozen=True)
class RAGQueryPlan:
    """Resolved retrieval intent — strategy/top_k parsed once at the seam.

    Passed to the retrieval stage as data instead of N positional args
    (question/dataset/top_k/strategy that used to be smuggled through 5 hops).
    """

    question: str
    dataset_name: str
    top_k: int
    strategy: str  # resolved: None → config.default_retrieval_strategy
    rerank_top_n: int

    @classmethod
    def resolve(
        cls,
        question: str,
        dataset_name: str,
        top_k: int | None,
        strategy: str | None,
        config: RAGConfig,
    ) -> RAGQueryPlan:
        """Build a plan, applying the None → config-default resolution in one place."""
        resolved_strategy = strategy or config.default_retrieval_strategy
        resolved_top_k = top_k or config.default_top_k
        return cls(
            question=question,
            dataset_name=dataset_name,
            top_k=resolved_top_k,
            strategy=resolved_strategy,
            rerank_top_n=config.reranker_top_n or resolved_top_k,
        )
