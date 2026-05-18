"""Ingest Flow — parallel file ingestion with foreach + retry + catch.

Scans a source directory, fans out one step per file via @foreach,
and collects results (success + dead-letter) in a join step.

Run locally::

    python flows/ingest_flow.py run

Run with custom parameters::

    python flows/ingest_flow.py run --source-path ./data/input --dataset-name docs
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
class IngestFlow(ArrowLakeFlowSpec, FlowSpec):
    """Parallel ingest: foreach per-file with dead-letter queue."""

    base_uri: str = Parameter("base-uri", default="./data/lake")
    config_path: str = Parameter("config-path", default="")
    dataset_name: str = Parameter("dataset-name", default="documents")
    source_path: str = Parameter("source-path", default="./data/input")

    @step
    def start(self) -> None:
        """Scan source directory, build file list for foreach."""
        from pathlib import Path

        self.config = self._load_config()
        self._auto_tag()

        source = Path(self.source_path)
        if not source.exists():
            self.files: list[str] = []
        else:
            self.files = sorted(
                str(f) for f in source.rglob("*") if f.is_file()
            )

        self.next(self.ingest_file, foreach="files")

    @step
    def ingest_file(self) -> None:
        """Process a single file. Failures become dead-letter entries."""
        import structlog

        logger = structlog.get_logger(__name__)

        try:
            from arrow_lake.ingest.ingestor import Ingestor
            from arrow_lake.ingest.storage import LanceStorageManager

            storage = LanceStorageManager(base_uri=self.base_uri)
            ingestor = Ingestor(storage)
            report = ingestor.ingest(self.dataset_name, [self.input])
            self.result: dict[str, Any] = {
                "file": self.input,
                "status": "success",
                "rows_ingested": report.total_rows,
            }
            logger.info(
                "ingest_file_complete",
                file=self.input,
                rows=report.total_rows,
            )
        except Exception as exc:
            self.result = {
                "file": self.input,
                "status": "failed",
                "error": str(exc),
            }
            logger.warning(
                "ingest_file_dead_letter",
                file=self.input,
                error=str(exc),
            )

        self.next(self.join)

    @step
    def join(self, inputs: list) -> None:
        """Aggregate parallel results into success / failure buckets."""
        self.successes: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []

        for inp in inputs:
            if inp.result["status"] == "success":
                self.successes.append(inp.result)
            else:
                self.failures.append(inp.result)

        self.total_rows = sum(r["rows_ingested"] for r in self.successes)
        self.next(self.end)

    @step
    def end(self) -> None:
        """Print summary report."""
        import json

        summary = {
            "total_files": len(self.successes) + len(self.failures),
            "success": len(self.successes),
            "failed": len(self.failures),
            "total_rows_ingested": self.total_rows,
            "dead_letter": self.failures,
        }
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    IngestFlow()
