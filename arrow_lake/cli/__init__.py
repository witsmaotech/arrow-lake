"""Arrow Lake CLI — command-line interface for the data lakehouse.

Usage::

    arrow-lake --help
    arrow-lake serve --port 8000
    arrow-lake status
    arrow-lake catalog list
    arrow-lake ingest files <dataset> <paths>...
    arrow-lake search vector <dataset> --query "hello"
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _print_error(message: str) -> None:
    console.print(f"[red]Error:[/red] {message}")


def _print_warning(message: str) -> None:
    console.print(f"[yellow]Warning:[/yellow] {message}")


def _print_success(message: str) -> None:
    console.print(f"[green]Success:[/green] {message}")


def _json_output(data: dict[str, Any]) -> None:
    """Print machine-readable JSON output."""
    click.echo(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Lake factory
# ---------------------------------------------------------------------------


def _lake(base_uri: str, config_path: str | None):
    """Create a Lake instance with optional YAML config."""
    from arrow_lake import ArrowLakeConfig, Lake

    config = None
    if config_path:
        config = ArrowLakeConfig.from_yaml(config_path)
    return Lake(base_uri=base_uri, config=config)


def _get_lake(ctx: click.Context):
    """Create a Lake instance from click context (replaces 3-line boilerplate)."""
    return _lake(ctx.obj["base_uri"], ctx.obj.get("config_path"))


# ---------------------------------------------------------------------------
# Async runner
# ---------------------------------------------------------------------------


def _run_async(coro: Any) -> Any:
    """Run an async coroutine in the CLI context."""
    import asyncio

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Main group
# ---------------------------------------------------------------------------


@click.group()
@click.option("--base-uri", default="./data/lake", envvar="ARROW_LAKE_BASE_URI", help="Lake base URI")
@click.option("--config", "config_path", default=None, help="Path to YAML config file")
@click.version_option(package_name="arrow-lake", message="%(prog)s %(version)s")
@click.pass_context
def main(ctx: click.Context, base_uri: str, config_path: str | None) -> None:
    """Arrow Lake — Unified multimodal data lakehouse CLI."""
    ctx.ensure_object(dict)
    ctx.obj["base_uri"] = base_uri
    ctx.obj["config_path"] = config_path


# ---------------------------------------------------------------------------
# Top-level commands (preserved)
# ---------------------------------------------------------------------------


@main.command()
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", default=8000, type=int, help="Listen port")
@click.option("--reload", is_flag=True, help="Enable hot-reload for development")
def serve(host: str, port: int, reload: bool) -> None:
    """Start the Arrow Lake REST API server."""
    cmd = [sys.executable, "-m", "uvicorn", "arrow_lake.api.app:create_app"]
    cmd.extend(["--factory", "--host", host, "--port", str(port)])
    if reload:
        cmd.append("--reload")

    console.print(f"Starting Arrow Lake API on [cyan]http://{host}:{port}[/cyan]{' (reload)' if reload else ''}")
    console.print(f"Docs: http://{host}:{port}/docs")

    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        _print_success("Server stopped")
    except FileNotFoundError:
        _print_error("uvicorn not installed. Run: pip install 'uvicorn[standard]'")
        raise SystemExit(1) from None


@main.command()
def version() -> None:
    """Print version and dependency information."""
    import importlib.metadata as im

    table = Table(title="Arrow Lake Environment")
    table.add_column("Component", style="cyan")
    table.add_column("Version", style="green")

    try:
        al_version = im.version("arrow-lake")
    except im.PackageNotFoundError:
        al_version = "unknown"
    table.add_row("arrow-lake", al_version)
    table.add_row("python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    deps = ["daft", "ray", "metaflow", "lancedb", "pyarrow", "duckdb", "pydantic"]
    for dep in deps:
        try:
            ver = im.version(dep)
        except im.PackageNotFoundError:
            ver = "[dim]not installed[/dim]"
        table.add_row(dep, ver)

    console.print(table)


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """List all registered datasets (alias for catalog list)."""
    from arrow_lake.cli.catalog import catalog_list_cmd

    ctx.ensure_object(dict)
    ctx.invoke(catalog_list_cmd, as_json=False)


@main.command()
@click.option("--base-uri", default="./demo_data", help="Demo output directory")
@click.option("--no-cleanup", is_flag=True, help="Keep demo data after completion")
def demo(base_uri: str, no_cleanup: bool) -> None:
    """Run an interactive demo — no Docker or config needed."""
    import shutil
    import time

    import numpy as np
    import pyarrow as pa

    from arrow_lake import Lake

    start_time = time.time()
    console.rule("[bold cyan]Arrow Lake Demo[/bold cyan]")
    console.print("This demo creates synthetic data and runs three queries.\nNo Docker, no config files, no external dependencies.\n")

    lake = Lake(base_uri=base_uri)

    try:
        console.print("[bold]1. Creating synthetic dataset...[/bold]")
        rng = np.random.RandomState(42)
        n = 200
        dim = 32

        vectors = rng.randn(n, dim).astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        vectors = vectors / norms

        sentences = [
            "Machine learning models require large datasets for training",
            "Deep learning architectures use neural networks with many layers",
            "Data analytics helps businesses make informed decisions",
            "Python is the most popular language for data science",
            "Vector databases enable efficient similarity search",
            "Natural language processing transforms text into embeddings",
            "Computer vision models process images for classification",
            "Reinforcement learning trains agents through reward signals",
            "Time series forecasting predicts future values from historical data",
            "Graph neural networks operate on structured graph representations",
        ]

        table = pa.table(
            {
                "id": [f"doc_{i:04d}" for i in range(n)],
                "text_content": [sentences[i % len(sentences)] for i in range(n)],
                "category": [
                    "ml" if i % 4 == 0 else "dl" if i % 4 == 1 else "data" if i % 4 == 2 else "dev"
                    for i in range(n)
                ],
                "word_count": [8 + (i % 12) for i in range(n)],
                "text_embedding": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
            }
        )

        lake.create_dataset("demo_docs", table)
        console.print(f"   Created [cyan]demo_docs[/cyan] with {n} rows\n")

        console.print("[bold]2. Vector Search (top 5)[/bold]")
        query_vec = rng.randn(dim).astype(np.float32)
        query_vec = query_vec / np.linalg.norm(query_vec)
        vs_result = lake.search("demo_docs", query_vec.tolist(), top_k=5)

        vs_table = Table(title="Vector Search Results")
        vs_table.add_column("#", justify="right", style="dim")
        vs_table.add_column("ID")
        vs_table.add_column("Category")
        vs_table.add_column("Distance", justify="right", style="green")

        for rank in range(min(vs_result.row_count, 5)):
            row_id = vs_result.table.column("id")[rank].as_py()
            cat = vs_result.table.column("category")[rank].as_py()
            dist = vs_result.table.column("_distance")[rank].as_py()
            vs_table.add_row(str(rank + 1), row_id, cat, f"{dist:.4f}")

        console.print(vs_table)
        console.print()

        console.print("[bold]3. SQL Analytics[/bold]")
        olap_result = lake.olap_query(
            "demo_docs",
            "SELECT category, COUNT(*) as cnt, AVG(word_count) as avg_words "
            "FROM demo_docs GROUP BY category ORDER BY cnt DESC",
        )

        sql_table = Table(title="Category Statistics")
        sql_table.add_column("Category", style="cyan")
        sql_table.add_column("Count", justify="right")
        sql_table.add_column("Avg Words", justify="right", style="green")

        for i in range(olap_result.row_count):
            cat = olap_result.table.column("category")[i].as_py()
            cnt = olap_result.table.column("cnt")[i].as_py()
            avg = olap_result.table.column("avg_words")[i].as_py()
            sql_table.add_row(cat, str(cnt), f"{avg:.1f}")

        console.print(sql_table)
        console.print()

        console.print("[bold]4. Full-Text Search[/bold]")
        try:
            lake.create_fts_index("demo_docs")
            fts_result = lake.text_search("demo_docs", "machine learning", top_k=5)

            fts_table = Table(title="Full-Text Search: 'machine learning'")
            fts_table.add_column("#", justify="right", style="dim")
            fts_table.add_column("ID")
            fts_table.add_column("Score", justify="right", style="green")

            for rank in range(min(fts_result.row_count, 5)):
                row_id = fts_result.table.column("id")[rank].as_py()
                score = fts_result.table.column("_score")[rank].as_py()
                fts_table.add_row(str(rank + 1), row_id, f"{score:.4f}")

            console.print(fts_table)
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            console.print(f"   [yellow]FTS skipped[/yellow]: {exc}")
        console.print()

    finally:
        if not no_cleanup:
            for ds in lake.list_datasets():
                lake.delete_dataset(ds)
            shutil.rmtree(base_uri, ignore_errors=True)
            console.print("[dim]Demo data cleaned up.[/dim]")

    elapsed = time.time() - start_time
    console.rule()
    _print_success(f"Demo completed in {elapsed:.1f}s")
    if no_cleanup:
        console.print(f"Data preserved at: [cyan]{base_uri}[/cyan]")


@main.command("multimodal-demo")
@click.option("--base-uri", default="./demo_multimodal", help="Demo output directory")
@click.option("--no-cleanup", is_flag=True, help="Keep demo data after completion")
def multimodal_demo(base_uri: str, no_cleanup: bool) -> None:
    """Run the multimodal demo — text, images, structured data, one platform."""
    import os

    console.rule("[bold cyan]Arrow Lake Multimodal Demo[/bold cyan]")

    project_root = str(Path(__file__).parent.parent)
    env = {**os.environ, "PYTHONPATH": f"{project_root}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"}

    cmd = [sys.executable, str(Path(project_root) / "examples" / "ingestion" / "06_multimodal_demo.py")]
    cmd.extend(["--base-uri", base_uri])
    if no_cleanup:
        cmd.append("--no-cleanup")

    try:
        result = subprocess.run(cmd, check=True, env=env, capture_output=True, text=True)
        if result.stdout:
            click.echo(result.stdout, nl=False)
        if result.stderr:
            click.echo(result.stderr, nl=False)
    except FileNotFoundError:
        _print_error("Demo script not found")
        raise SystemExit(1) from None


# ---------------------------------------------------------------------------
# Register subgroups
# ---------------------------------------------------------------------------

from arrow_lake.cli import (  # noqa: E402 — runtime import for lazy loading
    audit,
    backup,
    catalog,
    config_cmd,
    embed,
    export_cmd,
    index_cmd,
    ingest,
    kg,
    lifecycle,
    lineage,
    maintenance,
    quality,
    query,
    rag,
    search,
)

main.add_command(catalog.catalog_group, name="catalog")
main.add_command(ingest.ingest_group, name="ingest")
main.add_command(search.search_group, name="search")
main.add_command(index_cmd.index_group, name="index")
main.add_command(query.query_group, name="query")
main.add_command(export_cmd.export_cmd, name="export")
main.add_command(embed.embed_group, name="embed")
main.add_command(quality.quality_group, name="quality")
main.add_command(backup.backup_group, name="backup")
main.add_command(kg.kg_group, name="kg")
main.add_command(rag.rag_group, name="rag")
main.add_command(audit.audit_group, name="audit")
main.add_command(lineage.lineage_group, name="lineage")
main.add_command(lifecycle.lifecycle_group, name="lifecycle")
main.add_command(config_cmd.config_group, name="config")
main.add_command(maintenance.maintenance_group, name="maintenance")

kg.kg_group.add_command(kg.traverser_group, name="traverser")
kg.kg_group.add_command(kg.algo_group, name="algo")
