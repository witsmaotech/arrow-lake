"""CLI index commands — create, list, manage vector and full-text search indexes."""

from __future__ import annotations

import click
from rich.table import Table

from arrow_lake.cli import _get_lake, _print_error, _print_success, console


@click.group()
def index_group() -> None:
    """Manage indexes (vector, full-text)."""


@index_group.command("vector")
@click.argument("dataset")
@click.option("--column", default="text_embedding", help="Vector column name")
@click.option("--metric", default=None, help="Distance metric (l2, cosine, dot)")
@click.option("--type", "index_type", default=None, help="Index type (IVF_PQ, HNSW, etc.)")
@click.option("--replace/--no-replace", default=True, help="Replace existing index")
@click.pass_context
def index_vector(
    ctx: click.Context, dataset: str, column: str, metric: str | None,
    index_type: str | None, replace: bool,
) -> None:
    """Create a vector index on a dataset."""
    lake = _get_lake(ctx)

    console.print(f"[dim]Creating vector index on '{column}' for '{dataset}'...[/dim]")

    try:
        info = lake.create_vector_index(
            dataset,
            metric=metric or "",
            vector_column=column,
            index_type=index_type or "",
            replace=replace,
        )
    except Exception as exc:
        _print_error(f"Vector index creation failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Vector index created on '{column}' for '{dataset}'")
    if info:
        console.print(f"  Index: {info}")


@index_group.command("fts")
@click.argument("dataset")
@click.option("--column", default=None, help="Text column to index (default: config)")
@click.option("--replace/--no-replace", default=True, help="Replace existing index")
@click.pass_context
def index_fts(
    ctx: click.Context, dataset: str, column: str | None, replace: bool,
) -> None:
    """Create a full-text search index on a dataset."""
    lake = _get_lake(ctx)

    console.print(f"[dim]Creating FTS index for '{dataset}'...[/dim]")

    try:
        lake.create_fts_index(dataset, fts_column=column, replace=replace)
    except Exception as exc:
        _print_error(f"FTS index creation failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"FTS index created for '{dataset}'")


@index_group.command("scalar")
@click.argument("dataset")
@click.option("--column", required=True, help="Column to index")
@click.option("--type", "index_type", default="BTREE", help="Scalar index type (BTREE/BITMAP/ZONEMAP)")
@click.option("--name", default=None, help="Optional explicit index name")
@click.option("--replace/--no-replace", default=True, help="Replace existing index")
@click.pass_context
def index_scalar(
    ctx: click.Context, dataset: str, column: str, index_type: str,
    name: str | None, replace: bool,
) -> None:
    """Create a scalar index on a column (accelerates .where() filters)."""
    lake = _get_lake(ctx)
    console.print(f"[dim]Creating scalar index on '{column}' for '{dataset}'...[/dim]")
    try:
        lake.create_scalar_index(
            dataset,
            column=column,
            index_type=index_type,
            index_name=name,
            replace=replace,
        )
    except Exception as exc:
        _print_error(f"Scalar index creation failed: {exc}")
        raise SystemExit(1) from None
    _print_success(f"Scalar index ({index_type}) created on '{column}' for '{dataset}'")


@index_group.command("facets")
@click.argument("dataset")
@click.option("--columns", default=None, help="Comma-separated columns (default: config facet columns)")
@click.pass_context
def index_facets(ctx: click.Context, dataset: str, columns: str | None) -> None:
    """Create scalar indexes on facet columns in bulk."""
    lake = _get_lake(ctx)
    cols = [c.strip() for c in columns.split(",")] if columns else None
    console.print(f"[dim]Creating facet indexes for '{dataset}'...[/dim]")
    try:
        results = lake.create_facet_indexes(dataset, columns=cols)
    except Exception as exc:
        _print_error(f"Facet index creation failed: {exc}")
        raise SystemExit(1) from None
    for col, status in (results or {}).items():
        console.print(f"  {col}: {status}")
    _print_success(f"Facet indexes processed for '{dataset}'")


@index_group.command("list-vector")
@click.argument("dataset")
@click.pass_context
def index_list_vector(ctx: click.Context, dataset: str) -> None:
    """List all vector indexes on a dataset."""
    lake = _get_lake(ctx)

    try:
        indexes = lake.list_vector_indexes(dataset)
    except Exception as exc:
        _print_error(f"Failed to list vector indexes: {exc}")
        raise SystemExit(1) from None

    if not indexes:
        console.print("[dim]No vector indexes found.[/dim]")
        return

    table = Table(title=f"Vector Indexes: {dataset}")
    table.add_column("Name", style="cyan")
    table.add_column("Column")
    table.add_column("Type")
    table.add_column("Metric")
    for idx in indexes:
        table.add_row(
            getattr(idx, "name", str(idx)),
            getattr(idx, "column", ""),
            getattr(idx, "index_type", ""),
            getattr(idx, "metric", ""),
        )
    console.print(table)


@index_group.command("info-vector")
@click.argument("dataset")
@click.option("--column", default=None, help="Vector column name")
@click.pass_context
def index_info_vector(ctx: click.Context, dataset: str, column: str | None) -> None:
    """Get vector index info for a dataset."""
    lake = _get_lake(ctx)

    try:
        info = lake.get_vector_index_info(dataset, vector_column=column)
    except Exception as exc:
        _print_error(f"Failed to get vector index info: {exc}")
        raise SystemExit(1) from None

    if not info:
        console.print("[dim]No vector index found.[/dim]")
        return

    if isinstance(info, dict):
        table = Table(title="Vector Index Info")
        table.add_column("Property", style="cyan")
        table.add_column("Value")
        for k, v in info.items():
            table.add_row(k, str(v))
        console.print(table)
    else:
        console.print(info)


@index_group.command("rebuild-vector")
@click.argument("dataset")
@click.option("--column", default="text_embedding", help="Vector column name")
@click.option("--metric", default=None, help="Distance metric")
@click.option("--type", "index_type", default=None, help="Index type")
@click.option("--replace/--no-replace", default=True, help="Replace existing")
@click.pass_context
def index_rebuild_vector(
    ctx: click.Context, dataset: str, column: str, metric: str | None,
    index_type: str | None, replace: bool,
) -> None:
    """Rebuild a vector index on a dataset."""
    lake = _get_lake(ctx)

    try:
        lake.rebuild_vector_index(
            dataset,
            metric=metric or "",
            vector_column=column,
            index_type=index_type or "",
            replace=replace,
        )
    except Exception as exc:
        _print_error(f"Vector index rebuild failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Vector index rebuilt on '{column}' for '{dataset}'")


@index_group.command("delete-vector")
@click.argument("dataset")
@click.argument("index_name")
@click.pass_context
def index_delete_vector(ctx: click.Context, dataset: str, index_name: str) -> None:
    """Delete a vector index."""
    lake = _get_lake(ctx)

    try:
        lake.delete_vector_index(dataset, index_name)
    except Exception as exc:
        _print_error(f"Failed to delete vector index: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Vector index '{index_name}' deleted from '{dataset}'")


@index_group.command("info-fts")
@click.argument("dataset")
@click.pass_context
def index_info_fts(ctx: click.Context, dataset: str) -> None:
    """Get FTS index info for a dataset."""
    lake = _get_lake(ctx)

    try:
        info = lake.get_fts_index_info(dataset)
    except Exception as exc:
        _print_error(f"Failed to get FTS index info: {exc}")
        raise SystemExit(1) from None

    if not info:
        console.print("[dim]No FTS index found.[/dim]")
        return

    if isinstance(info, dict):
        table = Table(title="FTS Index Info")
        table.add_column("Property", style="cyan")
        table.add_column("Value")
        for k, v in info.items():
            table.add_row(k, str(v))
        console.print(table)
    else:
        console.print(info)


@index_group.command("delete-fts")
@click.argument("dataset")
@click.pass_context
def index_delete_fts(ctx: click.Context, dataset: str) -> None:
    """Delete FTS index from a dataset."""
    lake = _get_lake(ctx)

    try:
        lake.delete_fts_index(dataset)
    except Exception as exc:
        _print_error(f"Failed to delete FTS index: {exc}")
        raise SystemExit(1) from None

    _print_success(f"FTS index deleted from '{dataset}'")
