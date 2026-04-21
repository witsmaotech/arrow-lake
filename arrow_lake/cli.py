"""Arrow Lake CLI — command-line interface for common operations (Story 7.2).

Provides subcommands: serve, ingest, search, status, version.

Usage::

    arrow-lake --help
    arrow-lake version
    arrow-lake serve --port 8000
    arrow-lake status --base-uri ./data/lake
    arrow-lake ingest --source ./data.csv --table my_data
    arrow-lake search --query "machine learning" --top-k 10
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()


def _print_error(message: str) -> None:
    console.print(f"[red]Error:[/red] {message}")


def _print_warning(message: str) -> None:
    console.print(f"[yellow]Warning:[/yellow] {message}")


def _print_success(message: str) -> None:
    console.print(f"[green]Success:[/green] {message}")


@click.group()
@click.version_option(package_name="arrow-lake", message="%(prog)s %(version)s")
def main() -> None:
    """Arrow Lake — Unified multimodal data lakehouse CLI."""


@main.command()
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", default=8000, type=int, help="Listen port")
@click.option("--reload", is_flag=True, help="Enable hot-reload for development")
def serve(host: str, port: int, reload: bool) -> None:
    """Start the Arrow Lake REST API server."""
    import subprocess
    import sys

    cmd = [sys.executable, "-m", "uvicorn", "arrow_lake.api.app:create_app"]
    cmd.extend(["--factory", "--host", host, "--port", str(port)])
    if reload:
        cmd.append("--reload")

    console.print(
        f"Starting Arrow Lake API on [cyan]http://{host}:{port}[/cyan]"
        f'{" (reload)" if reload else ""}'
    )
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
    table.add_row(
        "python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )

    deps = ["daft", "ray", "metaflow", "lancedb", "pyarrow", "duckdb", "pydantic"]
    for dep in deps:
        try:
            ver = im.version(dep)
        except im.PackageNotFoundError:
            ver = "[dim]not installed[/dim]"
        table.add_row(dep, ver)

    console.print(table)


@main.command()
@click.option("--base-uri", default="./data/lake", help="Lake base URI")
def status(base_uri: str) -> None:
    """List all registered datasets with metadata."""
    try:
        from arrow_lake.ingest.storage import LanceStorageManager
    except ImportError as exc:
        _print_error(f"Cannot import storage: {exc}")
        raise SystemExit(1) from None

    storage = LanceStorageManager(base_uri=base_uri)

    try:
        tables = storage.list_datasets()
    except Exception as exc:
        _print_error(f"Cannot list tables: {exc}")
        raise SystemExit(1) from None

    if not tables:
        console.print("[dim]No datasets found.[/dim]")
        return

    table = Table(title=f"Datasets in {base_uri}")
    table.add_column("Name", style="cyan")
    table.add_column("Rows", justify="right")
    table.add_column("Columns")
    table.add_column("Version", justify="right")

    for name in sorted(tables):
        try:
            ds = storage.read_dataset(name)
            version = storage.get_version(name)
            cols = ", ".join(ds.column_names[:5])
            if len(ds.column_names) > 5:
                cols += ", ..."
            table.add_row(name, str(ds.num_rows), cols, str(version))
        except Exception as exc:
            table.add_row(name, "[red]error[/red]", str(exc), "-")

    console.print(table)
    _print_success(f"{len(tables)} dataset(s) listed")


@main.command()
@click.option("--source", required=True, help="Source file or directory to ingest")
@click.option("--table", "table_name", required=True, help="Target dataset name")
@click.option("--base-uri", default="./data/lake", help="Lake base URI")
@click.option("--modality", default="text", help="Data modality: text, image, mixed")
def ingest(source: str, table_name: str, base_uri: str, modality: str) -> None:
    """Ingest data into a Lance dataset."""
    try:
        from arrow_lake.ingest.storage import LanceStorageManager
    except ImportError as exc:
        _print_error(f"Cannot import storage: {exc}")
        raise SystemExit(1) from None

    path = Path(source)
    if not path.exists():
        _print_error(f"Source not found: {source}")
        raise SystemExit(1) from None

    console.print(
        f"Ingesting [cyan]{source}[/cyan] → [cyan]{table_name}[/cyan] (modality={modality})"
    )

    storage = LanceStorageManager(base_uri=base_uri)

    try:
        from arrow_lake.ingest.ingestor import Ingestor

        ingestor = Ingestor(storage)
        report = ingestor.ingest(table_name, [source])
        rows = getattr(report, "total_rows", 0)
        _print_success(f"Ingested {rows} rows into '{table_name}'")
    except Exception as exc:
        _print_error(f"Ingestion failed: {exc}")
        raise SystemExit(1) from None


@main.command()
@click.option("--query", required=True, help="Search query text")
@click.option("--table", "table_name", default="documents", help="Dataset to search")
@click.option("--top-k", default=10, help="Number of results")
@click.option("--base-uri", default="./data/lake", help="Lake base URI")
@click.option("--modality", default="text", help="Search modality: text, image")
@click.option("--alpha", default=None, type=float, help="Hybrid search alpha (vector weight)")
def search(
    query: str, table_name: str, top_k: int, base_uri: str, modality: str, alpha: float | None
) -> None:
    """Search across datasets."""
    console.print(f'Searching [cyan]"{query}"[/cyan] in [cyan]{table_name}[/cyan] (top_k={top_k})')

    try:
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(base_uri=base_uri)
        table = storage.read_dataset(table_name)
    except Exception as exc:
        _print_error(f"Cannot read dataset: {exc}")
        raise SystemExit(1) from None

    if table.num_rows == 0:
        _print_warning("Dataset is empty")
        return

    # Check for vector column
    if "vector" in table.column_names:
        import numpy as np

        vectors = np.stack(table.column("vector").to_pylist())
        # Use query hash as a deterministic pseudo-embedding when no real encoder is available
        rng = np.random.RandomState(int(hashlib.sha256(query.encode()).hexdigest(), 16) % (2**31))
        dim = vectors.shape[1]
        query_emb = rng.randn(dim).astype(np.float32)
        query_emb = query_emb / np.linalg.norm(query_emb)
        similarities = vectors @ query_emb
        top_indices = np.argsort(similarities)[::-1][:top_k]

        result_table = Table(title=f"Search Results (top {top_k})")
        result_table.add_column("#", justify="right", style="dim")
        result_table.add_column("ID")
        result_table.add_column("Score", justify="right", style="green")

        for rank, idx in enumerate(top_indices, 1):
            row_id = table.column("id")[idx].as_py() if "id" in table.column_names else str(idx)
            score = float(similarities[idx])
            result_table.add_row(str(rank), row_id, f"{score:.4f}")

        console.print(result_table)
        _print_success(f"{min(top_k, table.num_rows)} result(s)")
    else:
        # Fallback: text matching
        import pyarrow.compute as pc

        pattern = query.lower()
        mask = (
            pc.match_substring(table.column("text_content"), pattern)
            if "text_content" in table.column_names
            else None
        )

        if mask is not None:
            matched = table.filter(mask)
            results = matched.slice(0, top_k)
        else:
            results = table.slice(0, min(top_k, table.num_rows))

        if results.num_rows == 0:
            _print_warning("No results found")
            return

        result_table = Table(title="Search Results")
        result_table.add_column("#", justify="right", style="dim")
        for col in results.column_names[:4]:
            result_table.add_column(col)

        for i in range(results.num_rows):
            row_vals = [str(results.column(c)[i].as_py()) for c in results.column_names[:4]]
            result_table.add_row(str(i + 1), *row_vals)

        console.print(result_table)
        _print_success(f"{results.num_rows} result(s)")


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
    console.print(
        "This demo creates synthetic data and runs three queries.\n"
        "No Docker, no config files, no external dependencies.\n"
    )

    lake = Lake(base_uri=base_uri)

    try:
        # --- Generate synthetic data ---
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
                    "ml" if i % 4 == 0
                    else "dl" if i % 4 == 1
                    else "data" if i % 4 == 2
                    else "dev"
                    for i in range(n)
                ],
                "word_count": [8 + (i % 12) for i in range(n)],
                "text_embedding": pa.FixedSizeListArray.from_arrays(
                    vectors.ravel(), dim
                ),
            }
        )

        lake.create_dataset("demo_docs", table)
        console.print(f"   Created [cyan]demo_docs[/cyan] with {n} rows\n")

        # --- Vector search ---
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

        # --- SQL analytics ---
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

        # --- Full-text search ---
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
        except Exception as exc:
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


@main.command()
@click.option("--base-uri", default="./demo_multimodal", help="Demo output directory")
@click.option("--no-cleanup", is_flag=True, help="Keep demo data after completion")
def multimodal_demo(base_uri: str, no_cleanup: bool) -> None:
    """Run the multimodal demo — text, images, structured data, one platform."""
    import sys

    console.rule("[bold cyan]Arrow Lake Multimodal Demo[/bold cyan]")

    project_root = str(Path(__file__).parent.parent)
    os = __import__("os")
    env = {**os.environ, "PYTHONPATH": f"{project_root}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"}

    cmd = [sys.executable, str(Path(project_root) / "examples" / "ingestion" / "06_multimodal_demo.py")]
    cmd.extend(["--base-uri", base_uri])
    if no_cleanup:
        cmd.append("--no-cleanup")

    try:
        result = subprocess.run(cmd, check=True, env=env, capture_output=True, text=True)
        # Forward subprocess output through Click so CliRunner captures it
        if result.stdout:
            click.echo(result.stdout, nl=False)
        if result.stderr:
            click.echo(result.stderr, nl=False)
    except FileNotFoundError:
        _print_error("Demo script not found")
        raise SystemExit(1) from None
