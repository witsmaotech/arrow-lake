"""CLI rag commands — RAG query, stream, batch, feedback, and session management."""

from __future__ import annotations

import json

import click
from rich.table import Table

from arrow_lake.cli import _get_lake, _print_error, _print_success, _run_async, console


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
    lake = _get_lake(ctx)

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


@rag_group.command("stream")
@click.argument("dataset")
@click.argument("question")
@click.option("--top-k", default=5, help="Number of context chunks")
@click.option("--strategy", default=None, help="Retrieval strategy")
@click.option("--template", "template_name", default=None, help="Prompt template name")
@click.option("--session-id", default=None, help="Session ID for multi-turn")
@click.pass_context
def rag_stream(
    ctx: click.Context, dataset: str, question: str,
    top_k: int, strategy: str | None, template_name: str | None, session_id: str | None,
) -> None:
    """Stream a RAG query response chunk by chunk."""
    lake = _get_lake(ctx)

    async def _do_stream() -> None:
        async for chunk in lake.rag_query_stream(
            question, dataset,
            top_k=top_k, strategy=strategy,
            template_name=template_name, session_id=session_id,
        ):
            click.echo(chunk, nl=False)
        click.echo()

    try:
        _run_async(_do_stream())
    except Exception as exc:
        _print_error(f"RAG stream failed: {exc}")
        raise SystemExit(1) from None


@rag_group.command("extract")
@click.argument("dataset")
@click.option("--text-column", default=None, help="Text column to extract from")
@click.option("--top-k", default=10, help="Number of context chunks")
@click.option("--template", "template_name", default=None, help="Prompt template name")
@click.pass_context
def rag_extract(
    ctx: click.Context, dataset: str, text_column: str | None,
    top_k: int, template_name: str | None,
) -> None:
    """Extract entities from a dataset using RAG pipeline."""
    lake = _get_lake(ctx)

    try:
        entities = _run_async(
            lake.rag_extract(
                dataset,
                text_column=text_column,
                top_k=top_k,
                template_name=template_name,
            )
        )
    except Exception as exc:
        _print_error(f"Entity extraction failed: {exc}")
        raise SystemExit(1) from None

    if entities:
        console.print(json.dumps(entities, indent=2, default=str))
    else:
        console.print("[dim]No entities extracted.[/dim]")


@rag_group.command("history")
@click.argument("session_id")
@click.pass_context
def rag_history(ctx: click.Context, session_id: str) -> None:
    """Show conversation history for a session."""
    lake = _get_lake(ctx)

    try:
        history = lake.rag_get_history(session_id)
    except Exception as exc:
        _print_error(f"Failed to get history: {exc}")
        raise SystemExit(1) from None

    if not history:
        console.print("[dim]No history found for this session.[/dim]")
        return

    table = Table(title=f"Session History: {session_id}")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Question")
    table.add_column("Answer")

    for i, turn in enumerate(history, 1):
        if isinstance(turn, dict):
            table.add_row(str(i), turn.get("question", ""), turn.get("answer", "")[:200])
        else:
            table.add_row(str(i), str(turn), "")

    console.print(table)


@rag_group.command("batch")
@click.argument("dataset")
@click.option("--questions", required=True, help="JSON array of questions")
@click.option("--top-k", default=5, help="Number of context chunks per query")
@click.option("--strategy", default=None, help="Retrieval strategy")
@click.option("--concurrency", default=5, help="Max concurrent queries")
@click.pass_context
def rag_batch(
    ctx: click.Context, dataset: str, questions: str,
    top_k: int, strategy: str | None, concurrency: int,
) -> None:
    """Run batch RAG queries (multiple questions at once)."""
    try:
        question_list = json.loads(questions)
        if not isinstance(question_list, list):
            raise ValueError("Questions must be a JSON array")
    except (json.JSONDecodeError, ValueError) as exc:
        _print_error(f"Invalid --questions: {exc}")
        raise SystemExit(1) from None

    lake = _get_lake(ctx)

    console.print(f"[dim]Running {len(question_list)} queries (concurrency={concurrency})...[/dim]")

    try:
        results = _run_async(
            lake.rag_batch_query(
                question_list, dataset,
                top_k=top_k, strategy=strategy, concurrency=concurrency,
            )
        )
    except Exception as exc:
        _print_error(f"Batch query failed: {exc}")
        raise SystemExit(1) from None

    for i, response in enumerate(results, 1):
        console.print(f"\n[bold cyan]Q{i}:[/bold cyan]")
        if hasattr(response, "answer"):
            console.print(response.answer)
            if hasattr(response, "latency_ms") and response.latency_ms:
                console.print(f"[dim]Latency: {response.latency_ms}ms[/dim]")
        else:
            console.print(str(response))

    _print_success(f"Batch completed: {len(results)} queries")


@rag_group.command("feedback")
@click.argument("session_id")
@click.argument("turn_id", type=int)
@click.argument("rating", type=click.Choice(["positive", "negative", "neutral"]))
@click.option("--comment", default="", help="Feedback comment")
@click.pass_context
def rag_feedback(
    ctx: click.Context, session_id: str, turn_id: int, rating: str, comment: str,
) -> None:
    """Submit feedback on a RAG response."""
    lake = _get_lake(ctx)

    try:
        lake.rag_feedback(session_id, turn_id, rating, comment=comment)
    except Exception as exc:
        _print_error(f"Failed to submit feedback: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Feedback recorded: {rating} for session '{session_id}' turn {turn_id}")


@rag_group.command("get-feedback")
@click.argument("session_id")
@click.pass_context
def rag_get_feedback(ctx: click.Context, session_id: str) -> None:
    """Get all feedback for a session."""
    lake = _get_lake(ctx)

    try:
        feedback = lake.rag_get_feedback(session_id)
    except Exception as exc:
        _print_error(f"Failed to get feedback: {exc}")
        raise SystemExit(1) from None

    if not feedback:
        console.print("[dim]No feedback found for this session.[/dim]")
        return

    table = Table(title=f"Feedback: {session_id}")
    table.add_column("Turn", justify="right", style="dim")
    table.add_column("Rating")
    table.add_column("Comment")

    for entry in feedback:
        if isinstance(entry, dict):
            table.add_row(
                str(entry.get("turn_id", "")),
                entry.get("rating", ""),
                str(entry.get("comment", "")),
            )
        else:
            table.add_row(str(entry), "", "")

    console.print(table)


@rag_group.command("cleanup-sessions")
@click.pass_context
def rag_cleanup_sessions(ctx: click.Context) -> None:
    """Remove expired RAG sessions based on TTL."""
    lake = _get_lake(ctx)

    try:
        count = lake.rag_cleanup_expired_sessions()
    except Exception as exc:
        _print_error(f"Session cleanup failed: {exc}")
        raise SystemExit(1) from None

    if count > 0:
        _print_success(f"Cleaned up {count} expired session(s)")
    else:
        console.print("[dim]No expired sessions found.[/dim]")
