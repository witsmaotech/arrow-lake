"""CLI query commands — SQL analytics and materialized views."""

from __future__ import annotations

import click
from rich.table import Table

from arrow_lake.cli import _lake, _print_error, _print_success, console


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
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

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
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    try:
        row_count = lake.materialize(
            dataset, sql, view_name=view_name, ttl_days=ttl_days,
        )
    except Exception as exc:
        _print_error(f"Materialization failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Materialized view '{view_name}' created ({row_count} rows)")
