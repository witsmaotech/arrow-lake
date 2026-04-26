"""CLI export command — export datasets to Parquet or CSV."""

from __future__ import annotations

import click

from arrow_lake.cli import _get_lake, _print_error, _print_success, console


@click.command()
@click.argument("dataset")
@click.option("--output", required=True, help="Output file path")
@click.option("--format", "fmt", default=None, help="Output format (parquet or csv)")
@click.option("--columns", default=None, help="Comma-separated columns to export")
@click.pass_context
def export_cmd(ctx: click.Context, dataset: str, output: str, fmt: str | None, columns: str | None) -> None:
    """Export a dataset to Parquet or CSV."""
    lake = _get_lake(ctx)

    col_list = columns.split(",") if columns else None

    try:
        lake.export(dataset, output, format=fmt, columns=col_list)
    except Exception as exc:
        _print_error(f"Export failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Exported '{dataset}' to {output}")
