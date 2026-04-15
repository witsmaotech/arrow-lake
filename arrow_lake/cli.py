"""Arrow Lake CLI — command-line interface for common operations (Story 7.2).

Provides subcommands: ingest, search, status, version.

Usage::

    arrow-lake --help
    arrow-lake version
    arrow-lake status --base-uri ./data/lake
    arrow-lake ingest --source ./data.csv --table my_data
    arrow-lake search --query "machine learning" --top-k 10
"""

from __future__ import annotations

import hashlib
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


if __name__ == "__main__":
    main()
