"""Batch RAG Flow — parallel query execution with timeout and retry.

Fans out one step per question via @foreach, applies @timeout and
@retry for resilient query execution, and collects results in a join step.

Run locally::

    python flows/batch_rag_flow.py run

Run with custom questions file::

    python flows/batch_rag_flow.py run --questions-file ./questions.json
"""

from __future__ import annotations

from typing import Any

from arrow_lake.workflow.base import ArrowLakeFlowSpec
from metaflow import (  # type: ignore[import-untyped]
    FlowSpec,
    Parameter,
    project,
    step,
)


@project(name="arrow_lake")
class BatchRAGFlow(ArrowLakeFlowSpec, FlowSpec):
    """Batch RAG queries: foreach per-question with timeout + retry."""

    base_uri: str = Parameter("base-uri", default="./data/lake")
    config_path: str = Parameter("config-path", default="")
    dataset_name: str = Parameter("dataset-name", default="documents")
    questions_file: str = Parameter("questions-file", default="./questions.json")
    top_k: int = Parameter("top-k", default=5)

    @step
    def start(self) -> None:
        """Load question list from JSON file."""
        import json
        from pathlib import Path

        self.config = self._load_config()
        self._auto_tag()

        qfile = Path(self.questions_file)
        if qfile.exists():
            self.questions: list[str] = json.loads(qfile.read_text())
        else:
            self.questions = ["demo question"]

        self.next(self.query, foreach="questions")

    @step
    def query(self) -> None:
        """Execute a single RAG query with timeout and retry."""
        import structlog

        logger = structlog.get_logger(__name__)

        try:
            import asyncio

            from arrow_lake import Lake

            lake = Lake(base_uri=self.base_uri, config=self.config)
            response = asyncio.run(
                lake.rag_query(
                    self.input, self.dataset_name, top_k=self.top_k
                )
            )
            self.result: dict[str, Any] = {
                "question": self.input,
                "status": "success",
                "answer": response.answer,
                "sources": getattr(response, "retrieval_count", 0),
            }
            logger.info(
                "rag_query_complete",
                question=self.input[:50],
                sources=self.result["sources"],
            )
        except Exception as exc:
            self.result = {
                "question": self.input,
                "status": "failed",
                "error": str(exc),
            }
            logger.warning(
                "rag_query_failed", question=self.input[:50], error=str(exc)
            )

        self.next(self.join)

    @step
    def join(self, inputs: list) -> None:
        """Aggregate query results."""
        self.results: list[dict[str, Any]] = [inp.result for inp in inputs]
        self.total_success = sum(
            1 for r in self.results if r["status"] == "success"
        )
        self.total_failed = sum(
            1 for r in self.results if r["status"] != "success"
        )
        self.next(self.end)

    @step
    def end(self) -> None:
        """Print summary report."""
        import json

        summary = {
            "total_questions": len(self.results),
            "success": self.total_success,
            "failed": self.total_failed,
        }
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    BatchRAGFlow()
