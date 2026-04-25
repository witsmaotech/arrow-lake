"""CLI catalog commands — dataset management."""

from __future__ import annotations

import click
from rich.table import Table

from arrow_lake.cli import _lake, _print_error, _print_success, console


@click.group()
def catalog_group() -> None:
    """Manage datasets (list, info, delete)."""


@catalog_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def catalog_list_cmd(ctx: click.Context, as_json: bool) -> None:
    """List all registered datasets."""
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    try:
        datasets = lake.list_datasets()
    except Exception as exc:
        _print_error(f"Failed to list datasets: {exc}")
        raise SystemExit(1) from None

    if as_json:
        click.echo({"datasets": datasets})
        return

    if not datasets:
        console.print("[dim]No datasets found.[/dim]")
        return

    table = Table(title="Datasets")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Name", style="cyan")

    for i, name in enumerate(datasets, 1):
        table.add_row(str(i), name)

    console.print(table)


@catalog_group.command("info")
@click.argument("name")
@click.pass_context
def catalog_info_cmd(ctx: click.Context, name: str) -> None:
    """Show details of a dataset."""
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    try:
        if lake._storage is None:
            lake.list_datasets()
        table = lake._storage.open_dataset(name)
    except Exception as exc:
        _print_error(f"Failed to open dataset '{name}': {exc}")
        raise SystemExit(1) from None

    schema = table.schema
    row_count = table.count_rows()

    info_table = Table(title=f"Dataset: {name}")
    info_table.add_column("Property", style="cyan")
    info_table.add_column("Value")

    info_table.add_row("Rows", str(row_count))
    info_table.add_row("Columns", str(len(schema.names)))

    try:
        version = table.version
        info_table.add_row("Version", str(version))
    except Exception:
        pass

    col_table = Table(title="Schema")
    col_table.add_column("Column", style="cyan")
    col_table.add_column("Type")
    col_table.add_column("Nullable")

    for field in schema:
        col_table.add_row(field.name, str(field.type), str(field.nullable))

    console.print(info_table)
    console.print(col_table)


@catalog_group.command("delete")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip confirmation")
@click.pass_context
def catalog_delete_cmd(ctx: click.Context, name: str, yes: bool) -> None:
    """Delete a dataset."""
    if not yes:
        if not click.confirm(f"Delete dataset '{name}'? This cannot be undone."):
            return

    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    try:
        lake.delete_dataset(name)
    except Exception as exc:
        _print_error(f"Failed to delete dataset '{name}': {exc}")
        raise SystemExit(1) from None

    _print_success(f"Dataset '{name}' deleted")
