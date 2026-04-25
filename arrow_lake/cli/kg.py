"""CLI kg commands — knowledge graph build, query, stats."""

from __future__ import annotations

import json

import click
from rich.table import Table

from arrow_lake.cli import _lake, _print_error, _print_success, _run_async, console


@click.group()
def kg_group() -> None:
    """Knowledge graph operations."""


@kg_group.command("build")
@click.argument("dataset")
@click.pass_context
def kg_build(ctx: click.Context, dataset: str) -> None:
    """Build knowledge graph from a dataset."""
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    console.print(f"[dim]Building knowledge graph from '{dataset}'...[/dim]")

    try:
        task_id = _run_async(lake.kg_build(dataset))
    except Exception as exc:
        _print_error(f"KG build failed: {exc}")
        raise SystemExit(1) from None

    console.print(f"  Task ID: {task_id}")
    _print_success("KG build started (use 'kg status <task_id>' to check progress)")


@kg_group.command("status")
@click.argument("task_id")
@click.pass_context
def kg_status(ctx: click.Context, task_id: str) -> None:
    """Check knowledge graph build status."""
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    try:
        status = _run_async(lake.kg_build_status(task_id))
    except Exception as exc:
        _print_error(f"Failed to get status: {exc}")
        raise SystemExit(1) from None

    if status is None:
        _print_error(f"Task '{task_id}' not found")
        raise SystemExit(1) from None

    console.print(json.dumps(status, indent=2, default=str))


@kg_group.command("stats")
@click.pass_context
def kg_stats(ctx: click.Context) -> None:
    """Show knowledge graph statistics."""
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    try:
        stats = _run_async(lake.kg_stats())
    except Exception as exc:
        _print_error(f"Failed to get stats: {exc}")
        raise SystemExit(1) from None

    table = Table(title="Knowledge Graph Stats")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    for key, value in stats.items():
        table.add_row(key.replace("_", " ").title(), str(value))

    console.print(table)


@kg_group.command("query")
@click.argument("gremlin_query")
@click.pass_context
def kg_query(ctx: click.Context, gremlin_query: str) -> None:
    """Execute a Gremlin query against the knowledge graph."""
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    try:
        results = _run_async(lake.kg_query(gremlin_query))
    except Exception as exc:
        _print_error(f"KG query failed: {exc}")
        raise SystemExit(1) from None

    if not results:
        console.print("[dim]No results.[/dim]")
        return

    console.print(json.dumps(results, indent=2, default=str))


@kg_group.command("neighbors")
@click.argument("entity_id")
@click.option("--depth", default=1, help="Traversal depth")
@click.pass_context
def kg_neighbors(ctx: click.Context, entity_id: str, depth: int) -> None:
    """Get neighbors of an entity in the knowledge graph."""
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    try:
        neighbors = _run_async(lake.kg_get_neighbors(entity_id, depth=depth))
    except Exception as exc:
        _print_error(f"Failed to get neighbors: {exc}")
        raise SystemExit(1) from None

    if not neighbors:
        console.print("[dim]No neighbors found.[/dim]")
        return

    console.print(json.dumps(neighbors, indent=2, default=str))


@kg_group.command("delete")
@click.option("--yes", is_flag=True, help="Skip confirmation")
@click.pass_context
def kg_delete(ctx: click.Context, yes: bool) -> None:
    """Delete all data from the knowledge graph (irreversible)."""
    if not yes:
        if not click.confirm("Delete ALL knowledge graph data? This cannot be undone."):
            return

    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    try:
        _run_async(lake.kg_delete_graph())
    except Exception as exc:
        _print_error(f"KG deletion failed: {exc}")
        raise SystemExit(1) from None

    _print_success("Knowledge graph deleted")
