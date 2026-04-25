"""CLI ingest commands — data ingestion from files, HTTP, images, documents, videos."""

from __future__ import annotations

import click
from rich.table import Table

from arrow_lake.cli import _lake, _print_error, _print_success, console


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
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    try:
        report = lake.ingest(dataset, list(paths))
    except Exception as exc:
        _print_error(f"Ingest failed: {exc}")
        raise SystemExit(1) from None

    _show_report(report, f"{len(paths)} file(s) -> {dataset}")


@ingest_group.command("http")
@click.argument("dataset")
@click.argument("urls", nargs=-1, required=True)
@click.pass_context
def ingest_http(ctx: click.Context, dataset: str, urls: tuple[str, ...]) -> None:
    """Ingest files from HTTP(S) URLs."""
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

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
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

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
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

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
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    try:
        report = lake.ingest_videos(dataset, list(paths))
    except Exception as exc:
        _print_error(f"Video ingest failed: {exc}")
        raise SystemExit(1) from None

    _show_report(report, f"{len(paths)} video(s) -> {dataset}")
