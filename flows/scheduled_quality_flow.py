"""Scheduled Quality Pipeline Flow (Story 6.6).

Demonstrates a Metaflow flow with cron schedule for periodic quality checks.
Can be deployed to Metaflow's scheduler for automated execution.

Run locally (ignores schedule)::

    python flows/scheduled_quality_flow.py run

Schedule with Metaflow::

    python flows/scheduled_quality_flow.py --help
"""

from __future__ import annotations

from arrow_lake.workflow.base import ArrowLakeFlowSpec
from arrow_lake.workflow.schedule import ScheduleConfig, build_schedule
from metaflow import FlowSpec, Parameter, project, step  # type: ignore[import-untyped]


@project(name="arrow_lake")
@build_schedule(ScheduleConfig(daily_time="08:00"))
class ScheduledQualityFlow(ArrowLakeFlowSpec, FlowSpec):
    """Daily scheduled quality check flow.

    Checks all registered datasets for quality issues and produces
    a structured report. Designed to run automatically via Metaflow scheduler.
    """

    base_uri: str = Parameter("base-uri", default="./data/lake")
    config_path: str = Parameter("config-path", default="")
    dataset_name: str = Parameter("dataset-name", default="documents")
    active_filters: str = Parameter("active-filters", default="text_length")

    @step
    def start(self) -> None:
        """Initialize pipeline."""
        self.config = self._load_config()
        self._auto_tag()
        self.next(self.check_quality)

    @step
    def check_quality(self) -> None:
        """Run quality filters on the dataset."""

        import structlog

        logger = structlog.get_logger(__name__)

        try:
            from arrow_lake import Lake

            lake = Lake(base_uri=self.base_uri, config=self.config)
            self.report = lake.quality_filter(self.dataset_name, self.active_filters)
            self.status = "passed"
            logger.info(
                "scheduled_quality_check",
                dataset=self.dataset_name,
                status=self.status,
                passed=self.report.passed,
                rejected=self.report.rejected,
            )
        except Exception as exc:
            self.status = "failed"
            self.error_message = str(exc)
            logger.error(
                "scheduled_quality_check_failed",
                dataset=self.dataset_name,
                error=str(exc),
            )
        self.next(self.end)

    @step
    def end(self) -> None:
        """Output quality report."""
        import json

        import structlog

        logger = structlog.get_logger(__name__)

        if self.status == "passed":
            report_json = self.report.to_json()
            logger.info("scheduled_quality_complete", report=json.loads(report_json))
            print(json.dumps(json.loads(report_json), indent=2))
        else:
            logger.error(
                "scheduled_quality_failed", error=getattr(self, "error_message", "unknown")
            )


if __name__ == "__main__":
    ScheduledQualityFlow()
