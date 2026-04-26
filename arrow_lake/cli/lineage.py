"""CLI lineage commands — data lineage tracking and querying."""

from __future__ import annotations

import json

import click
from rich.table import Table

from arrow_lake.cli import _get_lake, _print_error, _print_success, console


@click.group()
def lineage_group() -> None:
    """Data lineage operations."""


@lineage_group.command("record")
@click.argument("dataset")
@click.argument("operation")
@click.option("--sources", default=None, help="Comma-separated source dataset names")
@click.option("--transform-type", default=None, help="Transform type description")
@click.option("--actor", default="cli", help="Actor performing the operation")
@click.option("--metadata", default=None, help="JSON metadata")
@click.pass_context
def lineage_record(
    ctx: click.Context, dataset: str, operation: str, sources: str | None,
    transform_type: str | None, actor: str, metadata: str | None,
) -> None:
    """Record a lineage event for a dataset."""
    lake = _get_lake(ctx)

    source_list = [s.strip() for s in sources.split(",")] if sources else []
    try:
        metadata_dict = json.loads(metadata) if metadata else {}
    except json.JSONDecodeError as exc:
        _print_error(f"Invalid JSON in --metadata: {exc}")
        raise SystemExit(1) from None

    try:
        lake.lineage_record_event(
            dataset_name=dataset,
            operation=operation,
            source_datasets=source_list,
            transform_type=transform_type,
            actor=actor,
            metadata=metadata_dict,
        )
    except Exception as exc:
        _print_error(f"Failed to record lineage: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Lineage recorded: {operation} on '{dataset}'")


@lineage_group.command("history")
@click.argument("dataset")
@click.pass_context
def lineage_history(ctx: click.Context, dataset: str) -> None:
    """Show lineage history for a dataset."""
    lake = _get_lake(ctx)

    try:
        events = lake.lineage_history(dataset)
    except Exception as exc:
        _print_error(f"Failed to get lineage history: {exc}")
        raise SystemExit(1) from None

    if not events:
        console.print("[dim]No lineage events found.[/dim]")
        return

    table = Table(title=f"Lineage History: {dataset}")
    table.add_column("Timestamp")
    table.add_column("Operation", style="cyan")
    table.add_column("Sources")
    table.add_column("Actor")

    for event in events:
        if isinstance(event, dict):
            table.add_row(
                str(event.get("timestamp", "")),
                event.get("operation", ""),
                ", ".join(event.get("source_datasets", [])),
                event.get("actor", ""),
            )
        else:
            table.add_row(str(event), "", "", "")

    console.print(table)


@lineage_group.command("query")
@click.argument("sql")
@click.pass_context
def lineage_query(ctx: click.Context, sql: str) -> None:
    """Run a SQL query over lineage data."""
    lake = _get_lake(ctx)

    try:
        result = lake.lineage_query(sql)
    except Exception as exc:
        _print_error(f"Lineage query failed: {exc}")
        raise SystemExit(1) from None

    if result.num_rows == 0:
        console.print("[dim]Query returned 0 rows.[/dim]")
        return

    columns = result.column_names
    table = Table(title=f"Lineage Query Result ({result.num_rows} rows)")
    for col in columns:
        table.add_column(col)

    for i in range(min(result.num_rows, 50)):
        row_vals = []
        for col in columns:
            val = result.column(col)[i].as_py()
            if val is not None:
                text = str(val)
                if len(text) > 80:
                    text = text[:77] + "..."
                row_vals.append(text)
            else:
                row_vals.append("NULL")
        table.add_row(*row_vals)

    if result.num_rows > 50:
        console.print("[dim]Showing 50 of {result.num_rows} rows.[/dim]")

    console.print(table)
