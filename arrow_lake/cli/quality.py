"""CLI quality commands — deduplication and quality filtering."""

from __future__ import annotations

import click
from rich.table import Table

from arrow_lake.cli import _get_lake, _print_error, _print_success, console


@click.group()
def quality_group() -> None:
    """Data quality operations (dedup, filter)."""


@quality_group.command("dedup")
@click.argument("dataset")
@click.option("--strategy", type=click.Choice(["exact", "perceptual", "both"]), required=True, help="Dedup strategy")
@click.option("--action", type=click.Choice(["flag", "remove"]), required=True, help="Action on duplicates")
@click.option("--threshold", default=10, type=int, help="Hamming distance threshold for perceptual")
@click.pass_context
def quality_dedup(
    ctx: click.Context, dataset: str, strategy: str, action: str, threshold: int,
) -> None:
    """Run content deduplication on a dataset."""
    lake = _get_lake(ctx)

    console.print(f"[dim]Running {strategy} dedup ({action}) on '{dataset}'...[/dim]")

    try:
        result = lake.deduplicate(
            dataset,
            strategy=strategy,
            action=action,
            perceptual_threshold=threshold,
        )
    except Exception as exc:
        _print_error(f"Deduplication failed: {exc}")
        raise SystemExit(1) from None

    table = Table(title=f"Dedup Report: {dataset}")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    for attr in ("total_rows", "removed_rows", "kept_rows", "strategy", "action"):
        val = getattr(result, attr, None)
        if val is not None:
            table.add_row(attr.replace("_", " ").title(), str(val))

    console.print(table)
    _print_success("Deduplication complete")


@quality_group.command("filter")
@click.argument("dataset")
@click.option("--filters", required=True, help="Comma-separated filter names")
@click.option("--mode", type=click.Choice(["all", "any"]), default="all", help="Filter mode")
@click.pass_context
def quality_filter(
    ctx: click.Context, dataset: str, filters: str, mode: str,
) -> None:
    """Run quality filters on a dataset."""
    lake = _get_lake(ctx)

    filter_names = filters
    console.print(f"[dim]Running filters [{filter_names}] ({mode}) on '{dataset}'...[/dim]")

    try:
        report = lake.quality_filter(dataset, active_filters=filter_names, mode=mode)
    except Exception as exc:
        _print_error(f"Quality filter failed: {exc}")
        raise SystemExit(1) from None

    table = Table(title=f"Quality Filter Report: {dataset}")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    for attr in ("total_rows", "passed_rows", "filtered_rows", "filters_applied"):
        val = getattr(report, attr, None)
        if val is not None:
            table.add_row(attr.replace("_", " ").title(), str(val))

    console.print(table)
    _print_success("Quality filter complete")
