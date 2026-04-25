"""CLI search commands — vector, full-text, and hybrid search."""

from __future__ import annotations

import numpy as np
import click
from rich.table import Table

from arrow_lake.cli import _lake, _print_error, _print_success, console


@click.group()
def search_group() -> None:
    """Search datasets (vector, full-text, hybrid)."""


def _get_query_vector(text: str, model_name: str, column: str):
    """Encode text to vector using LocalEmbeddingEncoder."""
    from arrow_lake.embed.encoder import LocalEmbeddingEncoder

    encoder = LocalEmbeddingEncoder(model_name=model_name)
    embeddings = encoder.encode_column(
        __import__("pyarrow").table({"text": [text]}), column="text",
    )
    if embeddings.embedding_dim == 0:
        _print_error("Failed to encode query text")
        raise SystemExit(1) from None

    return encoder._load_model().encode([text], normalize_embeddings=True)[0].tolist()


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
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

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
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

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
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

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
