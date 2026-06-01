"""CLI commands for storage maintenance operations."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from arrow_lake.cli import _get_lake, _json_output

console = Console()


@click.group()
def maintenance_group() -> None:
    """Storage maintenance operations."""


@maintenance_group.command("status")
@click.pass_context
def maintenance_status(ctx: click.Context) -> None:
    """Show maintenance scheduler status."""
    lake = _get_lake(ctx)
    storage = lake._storage
    config = lake._config.storage

    table = Table(title="Storage Maintenance Status")
    table.add_column("Property", style="cyan")
    table.add_column("Value")

    table.add_row("Enabled", str(config.maintenance_enabled))
    table.add_row("Interval (seconds)", str(config.maintenance_interval_seconds))
    table.add_row("Compaction Threshold (fragments)", str(config.compaction_fragment_threshold))
    table.add_row("Version Retention (days)", str(config.version_retention_days))

    datasets = storage.list_datasets()
    table.add_row("Datasets", str(len(datasets)))

    console.print(table)


@maintenance_group.command("run")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def maintenance_run(ctx: click.Context, as_json: bool) -> None:
    """Run a maintenance cycle (compaction + version cleanup)."""

    lake = _get_lake(ctx)
    from arrow_lake.ingest.maintenance_scheduler import MaintenanceScheduler

    scheduler = MaintenanceScheduler(storage=lake._storage, config=lake._config.storage)
    report = scheduler.run_once()

    if as_json:
        _json_output({
            "compacted": report.datasets_compacted,
            "cleaned": report.datasets_cleaned,
            "fragments_before": report.total_fragments_before,
            "fragments_after": report.total_fragments_after,
            "versions_removed": report.total_versions_removed,
            "duration_seconds": report.duration_seconds,
        })
        return

    table = Table(title="Maintenance Report")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Datasets Compacted", str(report.datasets_compacted))
    table.add_row("Datasets Cleaned", str(report.datasets_cleaned))
    table.add_row("Fragments Before", str(report.total_fragments_before))
    table.add_row("Fragments After", str(report.total_fragments_after))
    table.add_row("Versions Removed", str(report.total_versions_removed))
    table.add_row("Duration (seconds)", f"{report.duration_seconds:.3f}")

    console.print(table)
