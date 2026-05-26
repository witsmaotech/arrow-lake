"""CLI ingest commands — data ingestion, create, append, upsert, row operations."""

from __future__ import annotations

from pathlib import Path

import click
import pyarrow as pa
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from arrow_lake.cli import _get_lake, _print_error, _print_success, console


def _read_data_file(path: str) -> pa.Table:
    """Read a data file into a PyArrow Table based on extension."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".csv":
        import pyarrow.csv as pcsv
        return pcsv.read_csv(p)
    elif ext == ".parquet":
        return pa.read_table(p)
    elif ext in (".json", ".jsonl"):
        return pa.read_json(p)
    else:
        raise ValueError(f"Unsupported file format: {ext}. Use .csv, .json, .jsonl, or .parquet")


@click.group()
def ingest_group() -> None:
    """Ingest data into datasets."""


def _show_report(report, label: str) -> None:
    """Display an IngestionReport in a Rich table."""
    table = Table(title=f"Ingestion: {label}")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    rows = getattr(report, "rows_ingested", None)
    if rows is not None:
        table.add_row("Rows ingested", str(rows))

    dataset = getattr(report, "dataset_name", None)
    if dataset:
        table.add_row("Dataset", dataset)

    for attr in ("files_processed", "errors", "duration_seconds"):
        val = getattr(report, attr, None)
        if val is not None:
            table.add_row(attr.replace("_", " ").title(), str(val))

    console.print(table)
    _print_success(label)


@ingest_group.command("files")
@click.argument("dataset")
@click.argument("paths", nargs=-1, required=True)
@click.pass_context
def ingest_files(ctx: click.Context, dataset: str, paths: tuple[str, ...]) -> None:
    """Ingest local files (CSV, JSON, JSONL, Parquet)."""
    lake = _get_lake(ctx)
    path_list = list(paths)

    if len(path_list) > 3:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"Ingesting {len(path_list)} files...", total=len(path_list))
            try:
                for file_path in path_list:
                    lake.ingest(dataset, [file_path])
                    progress.advance(task)
            except Exception as exc:
                _print_error(f"Ingest failed: {exc}")
                raise SystemExit(1) from None
        _print_success(f"Ingested {len(path_list)} file(s) -> {dataset}")
    else:
        try:
            report = lake.ingest(dataset, path_list)
        except Exception as exc:
            _print_error(f"Ingest failed: {exc}")
            raise SystemExit(1) from None
        _show_report(report, f"{len(path_list)} file(s) -> {dataset}")


@ingest_group.command("http")
@click.argument("dataset")
@click.argument("urls", nargs=-1, required=True)
@click.pass_context
def ingest_http(ctx: click.Context, dataset: str, urls: tuple[str, ...]) -> None:
    """Ingest files from HTTP(S) URLs."""
    lake = _get_lake(ctx)

    try:
        report = lake.ingest_http(dataset, list(urls))
    except Exception as exc:
        _print_error(f"HTTP ingest failed: {exc}")
        raise SystemExit(1) from None

    _show_report(report, f"{len(urls)} URL(s) -> {dataset}")


@ingest_group.command("images")
@click.argument("dataset")
@click.argument("paths", nargs=-1, required=True)
@click.pass_context
def ingest_images(ctx: click.Context, dataset: str, paths: tuple[str, ...]) -> None:
    """Ingest image files with thumbnails and EXIF metadata."""
    lake = _get_lake(ctx)

    try:
        report = lake.ingest_images(dataset, list(paths))
    except Exception as exc:
        _print_error(f"Image ingest failed: {exc}")
        raise SystemExit(1) from None

    _show_report(report, f"{len(paths)} image(s) -> {dataset}")


@ingest_group.command("documents")
@click.argument("dataset")
@click.argument("paths", nargs=-1, required=True)
@click.pass_context
def ingest_documents(ctx: click.Context, dataset: str, paths: tuple[str, ...]) -> None:
    """Ingest PDF documents (parse, chunk, write to Lance)."""
    lake = _get_lake(ctx)

    try:
        report = lake.ingest_documents(dataset, list(paths))
    except Exception as exc:
        _print_error(f"Document ingest failed: {exc}")
        raise SystemExit(1) from None

    _show_report(report, f"{len(paths)} document(s) -> {dataset}")


@ingest_group.command("videos")
@click.argument("dataset")
@click.argument("paths", nargs=-1, required=True)
@click.pass_context
def ingest_videos(ctx: click.Context, dataset: str, paths: tuple[str, ...]) -> None:
    """Ingest video files with keyframe extraction."""
    lake = _get_lake(ctx)

    try:
        report = lake.ingest_videos(dataset, list(paths))
    except Exception as exc:
        _print_error(f"Video ingest failed: {exc}")
        raise SystemExit(1) from None

    _show_report(report, f"{len(paths)} video(s) -> {dataset}")


@ingest_group.command("create")
@click.argument("name")
@click.option("--data", default=None, help="Data file (CSV/JSON/JSONL/Parquet)")
@click.pass_context
def ingest_create(ctx: click.Context, name: str, data: str | None) -> None:
    """Create a new dataset from a data file."""
    if not data:
        _print_error("--data is required")
        raise SystemExit(1) from None

    lake = _get_lake(ctx)

    try:
        table = _read_data_file(data)
    except Exception as exc:
        _print_error(f"Failed to read data file: {exc}")
        raise SystemExit(1) from None

    try:
        lake.create_dataset(name, table)
    except Exception as exc:
        _print_error(f"Failed to create dataset: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Dataset '{name}' created ({table.num_rows} rows)")


@ingest_group.command("append")
@click.argument("name")
@click.option("--data", required=True, help="Data file (CSV/JSON/JSONL/Parquet)")
@click.pass_context
def ingest_append(ctx: click.Context, name: str, data: str) -> None:
    """Append data to an existing dataset."""
    lake = _get_lake(ctx)

    try:
        table = _read_data_file(data)
    except Exception as exc:
        _print_error(f"Failed to read data file: {exc}")
        raise SystemExit(1) from None

    try:
        lake.append_dataset(name, table)
    except Exception as exc:
        _print_error(f"Failed to append to dataset: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Appended {table.num_rows} rows to '{name}'")


@ingest_group.command("upsert")
@click.argument("dataset")
@click.option("--data", required=True, help="Data file (CSV/JSON/JSONL/Parquet)")
@click.option("--on", required=True, help="Column name to match on for upsert")
@click.pass_context
def ingest_upsert(ctx: click.Context, dataset: str, data: str, on: str) -> None:
    """Upsert rows into a dataset (insert or update)."""
    lake = _get_lake(ctx)

    try:
        table = _read_data_file(data)
    except Exception as exc:
        _print_error(f"Failed to read data file: {exc}")
        raise SystemExit(1) from None

    try:
        result = lake.upsert(dataset, table, on=on)
    except Exception as exc:
        _print_error(f"Upsert failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Upserted {table.num_rows} rows into '{dataset}'")
    if result:
        console.print(f"  Result: {result}")


@ingest_group.command("delete-rows")
@click.argument("dataset")
@click.option("--where", required=True, help="SQL WHERE expression")
@click.pass_context
def ingest_delete_rows(ctx: click.Context, dataset: str, where: str) -> None:
    """Delete rows from a dataset matching a WHERE expression."""
    lake = _get_lake(ctx)

    try:
        count = lake.delete_rows(dataset, where)
    except Exception as exc:
        _print_error(f"Delete failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Deleted {count} row(s) from '{dataset}'")


@ingest_group.command("update-rows")
@click.argument("dataset")
@click.option("--where", required=True, help="SQL WHERE expression")
@click.option("--set", "update_values", required=True, help="JSON dict of column:value pairs")
@click.pass_context
def ingest_update_rows(
    ctx: click.Context, dataset: str, where: str, update_values: str,
) -> None:
    """Update rows in a dataset matching a WHERE expression."""
    import json

    try:
        values = json.loads(update_values)
    except json.JSONDecodeError as exc:
        _print_error(f"Invalid JSON in --set: {exc}")
        raise SystemExit(1) from None

    lake = _get_lake(ctx)

    try:
        count = lake.update_rows(dataset, where, values)
    except Exception as exc:
        _print_error(f"Update failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Updated {count} row(s) in '{dataset}'")
