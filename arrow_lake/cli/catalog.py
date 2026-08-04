"""CLI catalog commands — dataset management."""

from __future__ import annotations

import json

import click
from rich.table import Table

from arrow_lake.cli import (
    _get_lake,
    _get_output_format,
    _output_table,
    _print_error,
    _print_success,
    console,
)


@click.group()
def catalog_group() -> None:
    """Manage datasets (list, info, delete)."""


@catalog_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def catalog_list_cmd(ctx: click.Context, as_json: bool) -> None:
    """List all registered datasets."""
    lake = _get_lake(ctx)

    try:
        datasets = lake.list_datasets()
    except Exception as exc:
        _print_error(f"Failed to list datasets: {exc}")
        raise SystemExit(1) from None

    if as_json or _get_output_format(ctx) == "json":
        click.echo(json.dumps({"datasets": datasets}, indent=2))
        return

    if not datasets:
        console.print("[dim]No datasets found.[/dim]")
        return

    table = Table(title="Datasets")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Name", style="cyan")

    for i, name in enumerate(datasets, 1):
        table.add_row(str(i), name)

    _output_table(ctx, table)


@catalog_group.command("info")
@click.argument("name")
@click.pass_context
def catalog_info_cmd(ctx: click.Context, name: str) -> None:
    """Show details of a dataset."""
    lake = _get_lake(ctx)

    try:
        table = lake.open_dataset(name)
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

    _output_table(ctx, info_table)
    _output_table(ctx, col_table)


@catalog_group.command("delete")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip confirmation")
@click.option(
    "--no-cascade",
    is_flag=True,
    help="Only drop the Lance table; keep KG graph / KA dump / catalog metadata "
    "/ ACL / template bindings for same-name reuse.",
)
@click.pass_context
def catalog_delete_cmd(
    ctx: click.Context, name: str, yes: bool, no_cascade: bool
) -> None:
    """Delete a dataset."""
    scope = "Lance table only" if no_cascade else "dataset + derived assets (KG/KA/metadata/ACL)"
    if not yes and not click.confirm(
        f"Delete dataset '{name}' ({scope})? This cannot be undone."
    ):
        return

    lake = _get_lake(ctx)

    try:
        lake.delete_dataset(name, cascade=not no_cascade)
    except Exception as exc:
        _print_error(f"Failed to delete dataset '{name}': {exc}")
        raise SystemExit(1) from None

    _print_success(f"Dataset '{name}' deleted")


@catalog_group.command("rename")
@click.argument("name")
@click.argument("new_name")
@click.pass_context
def catalog_rename(ctx: click.Context, name: str, new_name: str) -> None:
    """Rename a dataset."""
    lake = _get_lake(ctx)

    try:
        lake.rename_dataset(name, new_name)
    except Exception as exc:
        _print_error(f"Rename failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Dataset '{name}' -> '{new_name}'")


@catalog_group.command("copy")
@click.argument("name")
@click.argument("new_name")
@click.pass_context
def catalog_copy(ctx: click.Context, name: str, new_name: str) -> None:
    """Copy a dataset."""
    lake = _get_lake(ctx)

    try:
        lake.copy_dataset(name, new_name)
    except Exception as exc:
        _print_error(f"Copy failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Dataset '{name}' copied to '{new_name}'")


@catalog_group.command("merge")
@click.option("--sources", required=True, help="Comma-separated source dataset names")
@click.argument("target")
@click.pass_context
def catalog_merge(ctx: click.Context, sources: str, target: str) -> None:
    """Merge multiple datasets into a target."""
    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    if len(source_list) < 2:
        _print_error("At least 2 source datasets required")
        raise SystemExit(1) from None

    lake = _get_lake(ctx)

    try:
        lake.merge_datasets(source_list, target)
    except Exception as exc:
        _print_error(f"Merge failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Merged {len(source_list)} datasets -> '{target}'")


@catalog_group.command("health")
@click.pass_context
def catalog_health(ctx: click.Context) -> None:
    """Show system health status."""
    lake = _get_lake(ctx)

    try:
        info = lake.health()
    except Exception as exc:
        _print_error(f"Health check failed: {exc}")
        raise SystemExit(1) from None

    table = Table(title="System Health")
    table.add_column("Component", style="cyan")
    table.add_column("Status")
    for key, value in info.items() if isinstance(info, dict) else []:
        table.add_row(key, str(value))
    if not isinstance(info, dict):
        table.add_row("status", str(info))
    _output_table(ctx, table)


@catalog_group.command("inspect")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def catalog_inspect(ctx: click.Context, name: str, as_json: bool) -> None:
    """Show detailed dataset metadata (catalog view)."""
    lake = _get_lake(ctx)

    try:
        result = lake.catalog()
    except Exception as exc:
        _print_error(f"Catalog query failed: {exc}")
        raise SystemExit(1) from None

    if as_json or _get_output_format(ctx) == "json":
        import json
        click.echo(json.dumps(result, indent=2, default=str))
        return

    if not result:
        console.print("[dim]No catalog data found.[/dim]")
        return

    table = Table(title=f"Catalog: {name}")
    table.add_column("Property", style="cyan")
    table.add_column("Value")
    for key, value in result.items() if isinstance(result, dict) else []:
        table.add_row(key, str(value))
    _output_table(ctx, table)
