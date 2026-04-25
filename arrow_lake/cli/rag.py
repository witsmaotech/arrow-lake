"""CLI rag commands — RAG query and template management."""

from __future__ import annotations

import click

from arrow_lake.cli import _lake, _print_error, _run_async, console


@click.group()
def rag_group() -> None:
    """RAG (Retrieval-Augmented Generation) operations."""


@rag_group.command("query")
@click.argument("dataset")
@click.argument("question")
@click.option("--top-k", default=5, help="Number of context chunks")
@click.option("--strategy", default=None, help="Retrieval strategy")
@click.option("--template", "template_name", default=None, help="Prompt template name")
@click.option("--session-id", default=None, help="Session ID for multi-turn")
@click.pass_context
def rag_query(
    ctx: click.Context, dataset: str, question: str,
    top_k: int, strategy: str | None, template_name: str | None, session_id: str | None,
) -> None:
    """Run a RAG query over a dataset."""
    base_uri = ctx.obj["base_uri"]
    config_path = ctx.obj.get("config_path")
    lake = _lake(base_uri, config_path)

    console.print("[dim]Running RAG query...[/dim]")

    try:
        response = _run_async(
            lake.rag_query(
                question, dataset,
                top_k=top_k, strategy=strategy,
                template_name=template_name, session_id=session_id,
            )
        )
    except Exception as exc:
        _print_error(f"RAG query failed: {exc}")
        raise SystemExit(1) from None

    console.print("\n[bold]Answer:[/bold]")
    console.print(response.answer)

    if response.citations:
        console.print(f"\n[bold]Citations:[/bold] ({len(response.citations)} sources)")
        for i, cite in enumerate(response.citations, 1):
            console.print(f"  {i}. {cite}")

    if response.latency_ms:
        console.print(f"\n[dim]Latency: {response.latency_ms}ms[/dim]")

    if response.context_tokens:
        console.print(f"[dim]Context tokens: {response.context_tokens}[/dim]")


@rag_group.command("templates")
@click.pass_context
def rag_templates(ctx: click.Context) -> None:
    """List available prompt templates."""
    try:
        from arrow_lake.rag.prompts import PromptRegistry
    except ImportError:
        console.print("[dim]PromptRegistry not available.[/dim]")
        return

    try:
        templates = PromptRegistry.list_templates()
    except Exception as exc:
        _print_error(f"Failed to list templates: {exc}")
        raise SystemExit(1) from None

    if not templates:
        console.print("[dim]No templates found.[/dim]")
        return

    from rich.table import Table

    table = Table(title="Prompt Templates")
    table.add_column("Name", style="cyan")
    table.add_column("Description")

    for t in templates:
        name = t if isinstance(t, str) else getattr(t, "name", str(t))
        desc = getattr(t, "description", "") if not isinstance(t, str) else ""
        table.add_row(name, desc)

    console.print(table)
