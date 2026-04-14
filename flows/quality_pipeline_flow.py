"""Quality Pipeline Flow (Story 6.1).

Demonstrates a Metaflow flow that:
1. Loads a dataset from Lance
2. Runs quality filters
3. Produces a quality report

Run locally::

    python flows/quality_pipeline_flow.py run

Run with config::

    python flows/quality_pipeline_flow.py run --config-path configs/dev.yaml
"""

from metaflow import FlowSpec, Parameter, step, project  # type: ignore[import-untyped]

from arrow_lake.workflow.base import ArrowLakeFlowSpec


@project(name="arrow_lake")
class QualityPipelineFlow(ArrowLakeFlowSpec, FlowSpec):
    """End-to-end quality pipeline flow."""

    base_uri: str = Parameter("base-uri", default="./data/lake")
    config_path: str = Parameter("config-path", default="")
    dataset_name: str = Parameter("dataset-name", default="documents")
    active_filters: str = Parameter("active-filters", default="text_length")

    @step
    def start(self) -> None:
        """Initialize: load config."""
        self.config = self._load_config()
        self._auto_tag()
        self.next(self.apply_filters)

    @step
    def apply_filters(self) -> None:
        """Apply quality filters to the dataset."""
        from arrow_lake import Lake

        lake = Lake(base_uri=self.base_uri, config=self.config)
        self.report = lake.quality_filter(
            self.dataset_name,
            self.active_filters,
        )
        self.next(self.end)

    @step
    def end(self) -> None:
        """Output quality report as JSON."""
        import json

        report_json = self.report.to_json()
        print(json.dumps(report_json, indent=2))


if __name__ == "__main__":
    QualityPipelineFlow()
