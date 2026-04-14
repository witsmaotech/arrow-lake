"""Tag-based run tracking (Story 6.7).

Generates automatic tags from run metadata for discovery and resume.
Tags follow the pattern: ``{category}:{value}``.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RunTags:
    """Set of auto-generated tags for a workflow run."""

    flow_name: str
    run_id: str
    dataset_name: str = ""
    status: str = "running"

    def to_list(self) -> list[str]:
        """Generate a tag list compatible with Metaflow ``@project``."""
        tags = [
            f"flow:{self.flow_name}",
            f"run_id:{self.run_id}",
            f"status:{self.status}",
        ]
        if self.dataset_name:
            tags.append(f"dataset:{self.dataset_name}")
        return tags


def generate_resume_tags(failed_run_id: str, flow_name: str) -> list[str]:
    """Generate tags for a resumed run.

    Args:
        failed_run_id: Run ID of the failed run being resumed.
        flow_name: Name of the flow.

    Returns:
        Tag list including resume metadata.
    """
    return [
        f"flow:{flow_name}",
        f"resumed_from:{failed_run_id}",
        "status:resumed",
    ]


def find_failed_runs(flow_name: str) -> list[str]:
    """Find failed run IDs for a given flow.

    Uses the Metaflow ``Flow`` API to query for failed runs.

    Args:
        flow_name: Name of the flow to search.

    Returns:
        List of failed run IDs (most recent first).
    """
    try:
        from metaflow import Flow  # type: ignore[import-untyped]

        flow = Flow(flow_name)
        failed: list[str] = []
        for run in flow.runs():
            if run.successful:
                continue
            failed.append(str(run.id))
        return failed
    except Exception as exc:
        logger.warning("workflow_find_failed_runs_error", error=str(exc))
        return []
