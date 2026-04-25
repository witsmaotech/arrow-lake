"""CLI index commands — create vector and full-text search indexes."""

from __future__ import annotations

import click

from arrow_lake.cli import _lake, _print_error, _print_success, console


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
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

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
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    console.print(f"[dim]Creating FTS index for '{dataset}'...[/dim]")

    try:
        lake.create_fts_index(dataset, fts_column=column, replace=replace)
    except Exception as exc:
        _print_error(f"FTS index creation failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"FTS index created for '{dataset}'")
