"""CLI query commands — SQL analytics and materialized views."""

from __future__ import annotations

import click
from rich.table import Table

from arrow_lake.cli import _get_lake, _print_error, _print_success, console


@click.group()
def query_group() -> None:
    """Run SQL queries and manage materialized views."""


def _display_arrow_table(result, max_rows: int = 50) -> None:
    """Display an OLAP query result as a Rich table."""
    if result.num_rows == 0:
        console.print("[dim]Query returned 0 rows.[/dim]")
        return

    columns = result.column_names
    display = Table(title=f"Query Result ({result.num_rows} rows)")
    for col in columns:
        display.add_column(col)

    for i in range(min(result.num_rows, max_rows)):
        row_vals = []
        for col in columns:
            val = result.column(col)[i].as_py()
            if val is not None:
                text = str(val)
                if len(text) > 80:
                    text = text[:77] + "..."
                row_vals.append(text)
            else:
                row_vals.append("NULL")
        display.add_row(*row_vals)

    if result.num_rows > max_rows:
        console.print(f"[dim]Showing {max_rows} of {result.num_rows} rows.[/dim]")

    console.print(display)


@query_group.command("sql")
@click.argument("dataset")
@click.option("--sql", required=True, help="SQL query to execute")
@click.option("--max-rows", default=100, help="Maximum rows to display")
@click.pass_context
def query_sql(ctx: click.Context, dataset: str, sql: str, max_rows: int) -> None:
    """Run a DuckDB SQL query on a dataset."""
    lake = _get_lake(ctx)

    try:
        result = lake.olap_query(dataset, sql, max_rows=max_rows)
    except Exception as exc:
        _print_error(f"SQL query failed: {exc}")
        raise SystemExit(1) from None

    _display_arrow_table(result, max_rows)


@query_group.command("materialize")
@click.argument("dataset")
@click.option("--sql", required=True, help="SQL query to materialize")
@click.option("--name", "view_name", required=True, help="Materialized view name")
@click.option("--ttl-days", default=None, type=int, help="TTL in days")
@click.pass_context
def query_materialize(
    ctx: click.Context, dataset: str, sql: str, view_name: str, ttl_days: int | None,
) -> None:
    """Materialize a SQL query result as a persistent view."""
    lake = _get_lake(ctx)

    try:
        row_count = lake.materialize(
            dataset, sql, view_name=view_name, ttl_days=ttl_days,
        )
    except Exception as exc:
        _print_error(f"Materialization failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Materialized view '{view_name}' created ({row_count} rows)")


@query_group.command("meta")
@click.argument("dataset")
@click.option("--sql", required=True, help="Metadata SQL query")
@click.option("--max-rows", default=100, help="Maximum rows to display")
@click.pass_context
def query_meta(ctx: click.Context, dataset: str, sql: str, max_rows: int) -> None:
    """Run a metadata SQL query on a dataset."""
    lake = _get_lake(ctx)

    try:
        result = lake.query(dataset, sql, max_rows=max_rows)
    except Exception as exc:
        _print_error(f"Metadata query failed: {exc}")
        raise SystemExit(1) from None

    _display_arrow_table(result, max_rows)


@query_group.command("cleanup-materialized")
@click.option("--ttl-days", default=None, type=int, help="TTL override in days")
@click.pass_context
def query_cleanup_materialized(ctx: click.Context, ttl_days: int | None) -> None:
    """Remove expired materialized views."""
    lake = _get_lake(ctx)

    try:
        cleaned = lake.cleanup_materialized(ttl_days=ttl_days)
    except Exception as exc:
        _print_error(f"Cleanup failed: {exc}")
        raise SystemExit(1) from None

    if cleaned:
        _print_success(f"Cleaned {len(cleaned)} expired view(s): {', '.join(cleaned)}")
    else:
        console.print("[dim]No expired materialized views found.[/dim]")


@query_group.command("daft")
@click.argument("dataset")
@click.option("--columns", default=None, help="Comma-separated columns to select")
@click.option("--limit", default=50, help="Maximum rows")
@click.pass_context
def query_daft(ctx: click.Context, dataset: str, columns: str | None, limit: int) -> None:
    """Load dataset as a Daft DataFrame and display."""
    lake = _get_lake(ctx)

    try:
        df = lake.daft_query(dataset)
    except Exception as exc:
        _print_error(f"Daft query failed: {exc}")
        raise SystemExit(1) from None

    if columns:
        col_list = [c.strip() for c in columns.split(",")]
        df = df.select(*col_list)

    collected = df.collect()
    pa_table = collected.to_arrow()

    _display_arrow_table(pa_table, limit)
