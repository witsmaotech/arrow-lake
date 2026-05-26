"""CLI search commands — vector, full-text, and hybrid search."""

from __future__ import annotations

import json

import click
from rich.table import Table

from arrow_lake.cli import _get_lake, _print_error, console


@click.group()
def search_group() -> None:
    """Search datasets (vector, full-text, hybrid)."""


_encoder_cache: dict[str, Any] = {}


def _get_encoder(model_name: str):
    """Get or create a cached LocalEmbeddingEncoder."""
    from arrow_lake.embed.encoder import LocalEmbeddingEncoder

    if model_name not in _encoder_cache:
        _encoder_cache[model_name] = LocalEmbeddingEncoder(model_name=model_name)
    return _encoder_cache[model_name]


def _get_query_vector(text: str, model_name: str, column: str):
    """Encode text to vector using cached LocalEmbeddingEncoder."""
    encoder = _get_encoder(model_name)
    raw = encoder._load_model().encode([text], normalize_embeddings=True)
    if raw is None or len(raw) == 0:
        _print_error("Failed to encode query text")
        raise SystemExit(1) from None

    return raw[0].tolist()


def _format_results(table, result_table, max_rows: int = 10) -> None:
    """Display search results in a Rich table."""
    if result_table.num_rows == 0:
        console.print("[dim]No results found.[/dim]")
        return

    columns = result_table.column_names
    id_col = "id" if "id" in columns else columns[0]
    score_col = "_distance" if "_distance" in columns else "_score" if "_score" in columns else None

    display = Table(title=f"Results ({result_table.num_rows} rows)")
    display.add_column("#", justify="right", style="dim")
    display.add_column("ID")

    if score_col:
        label = "Distance" if score_col == "_distance" else "Score"
        display.add_column(label, justify="right", style="green")

    for col in columns:
        if col not in (id_col, score_col, "_fts_segmented"):
            display.add_column(col)

    for i in range(min(result_table.num_rows, max_rows)):
        row_id = str(result_table.column(id_col)[i].as_py())
        row_vals = [str(i + 1), row_id]
        if score_col:
            score = result_table.column(score_col)[i].as_py()
            row_vals.append(f"{score:.4f}" if isinstance(score, float) else str(score))
        for col in columns:
            if col not in (id_col, score_col, "_fts_segmented"):
                val = result_table.column(col)[i].as_py()
                if val is not None:
                    text = str(val)
                    if len(text) > 80:
                        text = text[:77] + "..."
                    row_vals.append(text)
                else:
                    row_vals.append("—")
        display.add_row(*row_vals)

    console.print(display)


@search_group.command("vector")
@click.argument("dataset")
@click.option("--query", required=True, help="Search query text")
@click.option("--top-k", default=10, help="Number of results")
@click.option("--column", default="text_embedding", help="Vector column name")
@click.option("--model", default="Qwen/Qwen3-Embedding-0.6B", help="Embedding model")
@click.pass_context
def search_vector(
    ctx: click.Context, dataset: str, query: str, top_k: int, column: str, model: str,
) -> None:
    """Vector similarity search."""
    lake = _get_lake(ctx)

    console.print(f"[dim]Encoding query with {model}...[/dim]", end=" ")
    query_vec = _get_query_vector(query, model, column)
    console.print("[green]done[/green]")

    try:
        result = lake.search(dataset, query_vec, top_k=top_k, vector_column=column)
    except Exception as exc:
        _print_error(f"Vector search failed: {exc}")
        raise SystemExit(1) from None

    _format_results(result, result.table, top_k)


@search_group.command("fts")
@click.argument("dataset")
@click.option("--query", required=True, help="Search query text")
@click.option("--top-k", default=10, help="Number of results")
@click.option("--column", default=None, help="FTS column name (default: config)")
@click.pass_context
def search_fts(
    ctx: click.Context, dataset: str, query: str, top_k: int, column: str | None,
) -> None:
    """Full-text search (BM25)."""
    lake = _get_lake(ctx)

    try:
        result = lake.text_search(dataset, query, top_k=top_k, fts_column=column)
    except Exception as exc:
        _print_error(f"Full-text search failed: {exc}")
        raise SystemExit(1) from None

    _format_results(result, result.table, top_k)


@search_group.command("hybrid")
@click.argument("dataset")
@click.option("--query", required=True, help="Search query text")
@click.option("--top-k", default=10, help="Number of results")
@click.option("--vector-column", default="text_embedding", help="Vector column")
@click.option("--fts-column", default=None, help="FTS column (default: config)")
@click.option("--model", default="Qwen/Qwen3-Embedding-0.6B", help="Embedding model")
@click.pass_context
def search_hybrid(
    ctx: click.Context, dataset: str, query: str, top_k: int,
    vector_column: str, fts_column: str | None, model: str,
) -> None:
    """Hybrid search (vector + full-text RRF fusion)."""
    lake = _get_lake(ctx)

    console.print(f"[dim]Encoding query with {model}...[/dim]", end=" ")
    query_vec = _get_query_vector(query, model, vector_column)
    console.print("[green]done[/green]")

    try:
        result = lake.hybrid_search(
            dataset, query_vec, query,
            top_k=top_k, vector_column=vector_column, fts_column=fts_column,
        )
    except Exception as exc:
        _print_error(f"Hybrid search failed: {exc}")
        raise SystemExit(1) from None

    _format_results(result, result.table, top_k)


@search_group.command("faceted")
@click.argument("dataset")
@click.option("--query", required=True, help="Search query text")
@click.option("--facets", default=None, help="Comma-separated facet columns")
@click.option("--top-k", default=10, help="Number of results")
@click.option("--column", default="text_embedding", help="Vector column name")
@click.option("--model", default="Qwen/Qwen3-Embedding-0.6B", help="Embedding model")
@click.pass_context
def search_faceted(
    ctx: click.Context, dataset: str, query: str, facets: str | None,
    top_k: int, column: str, model: str,
) -> None:
    """Faceted search (vector + facet counts)."""
    lake = _get_lake(ctx)

    console.print(f"[dim]Encoding query with {model}...[/dim]", end=" ")
    query_vec = _get_query_vector(query, model, column)
    console.print("[green]done[/green]")

    facet_cols = [f.strip() for f in facets.split(",")] if facets else None

    try:
        result = lake.faceted_search(
            dataset, query_vec,
            facets=facet_cols,
            top_k=top_k,
            vector_column=column,
        )
    except Exception as exc:
        _print_error(f"Faceted search failed: {exc}")
        raise SystemExit(1) from None

    if hasattr(result, "table"):
        _format_results(result, result.table, top_k)

    if hasattr(result, "facet_counts") and result.facet_counts:
        facet_table = Table(title="Facet Counts")
        facet_table.add_column("Facet", style="cyan")
        facet_table.add_column("Value")
        facet_table.add_column("Count", justify="right")
        for facet_name, buckets in result.facet_counts.items():
            for bucket in buckets:
                if isinstance(bucket, dict):
                    facet_table.add_row(facet_name, str(bucket.get("value", "")), str(bucket.get("count", 0)))
                elif isinstance(bucket, (tuple, list)):
                    facet_table.add_row(facet_name, str(bucket[0]), str(bucket[1]))
        console.print(facet_table)


@search_group.command("ensemble")
@click.argument("dataset")
@click.option("--query", required=True, help="Search query text")
@click.option("--columns", required=True, help="Comma-separated embedding columns")
@click.option("--weights", default=None, help="JSON dict of column weights")
@click.option("--top-k", default=10, help="Number of results")
@click.option("--model", default="Qwen/Qwen3-Embedding-0.6B", help="Embedding model")
@click.pass_context
def search_ensemble(
    ctx: click.Context, dataset: str, query: str, columns: str,
    weights: str | None, top_k: int, model: str,
) -> None:
    """Ensemble search across multiple embedding columns."""
    lake = _get_lake(ctx)

    col_list = [c.strip() for c in columns.split(",")]
    try:
        weights_dict = json.loads(weights) if weights else None
    except json.JSONDecodeError as exc:
        _print_error(f"Invalid JSON in --weights: {exc}")
        raise SystemExit(1) from None

    console.print(f"[dim]Encoding query with {model}...[/dim]", end=" ")
    query_vec = _get_query_vector(query, model, col_list[0])
    console.print("[green]done[/green]")

    try:
        result = lake.ensemble_search(
            dataset, query_vec,
            columns=col_list,
            weights=weights_dict,
            top_k=top_k,
        )
    except Exception as exc:
        _print_error(f"Ensemble search failed: {exc}")
        raise SystemExit(1) from None

    _format_results(result, result.table, top_k)
