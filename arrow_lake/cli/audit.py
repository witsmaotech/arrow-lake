"""CLI audit commands — record, verify, query, export, and analyze audit entries."""

from __future__ import annotations

import json

import click
from rich.table import Table

from arrow_lake.cli import _get_lake, _print_error, _print_success, console


@click.group()
def audit_group() -> None:
    """Audit trail operations."""


@audit_group.command("record")
@click.argument("event_type")
@click.option("--dataset", default=None, help="Dataset name")
@click.option("--actor", default="cli", help="Actor performing the action")
@click.option("--payload", default=None, help="JSON payload")
@click.pass_context
def audit_record(
    ctx: click.Context, event_type: str, dataset: str | None,
    actor: str, payload: str | None,
) -> None:
    """Record an audit event."""
    lake = _get_lake(ctx)

    try:
        payload_dict = json.loads(payload) if payload else {}
    except json.JSONDecodeError as exc:
        _print_error(f"Invalid JSON in --payload: {exc}")
        raise SystemExit(1) from None

    try:
        audit_id = lake.audit_record(
            event_type=event_type,
            dataset_name=dataset,
            actor=actor,
            payload=payload_dict,
        )
    except Exception as exc:
        _print_error(f"Failed to record audit event: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Audit event recorded: {audit_id}")


@audit_group.command("verify")
@click.argument("audit_id")
@click.pass_context
def audit_verify(ctx: click.Context, audit_id: str) -> None:
    """Verify HMAC integrity of an audit entry."""
    lake = _get_lake(ctx)

    try:
        valid = lake.audit_verify(audit_id)
    except Exception as exc:
        _print_error(f"Verification failed: {exc}")
        raise SystemExit(1) from None

    if valid:
        _print_success(f"Audit '{audit_id}': [green]VALID[/green]")
    else:
        _print_error(f"Audit '{audit_id}': [red]INVALID[/red]")


@audit_group.command("query")
@click.option("--dataset", default=None, help="Filter by dataset")
@click.option("--start", default=None, help="Start timestamp (ISO)")
@click.option("--end", default=None, help="End timestamp (ISO)")
@click.option("--event-type", default=None, help="Filter by event type")
@click.pass_context
def audit_query(
    ctx: click.Context, dataset: str | None, start: str | None,
    end: str | None, event_type: str | None,
) -> None:
    """Query audit trail entries."""
    lake = _get_lake(ctx)

    try:
        results = lake.audit_query(
            dataset_name=dataset,
            start=start,
            end=end,
            event_type=event_type,
        )
    except Exception as exc:
        _print_error(f"Audit query failed: {exc}")
        raise SystemExit(1) from None

    if not results:
        console.print("[dim]No audit entries found.[/dim]")
        return

    table = Table(title=f"Audit Trail ({len(results)} entries)")
    table.add_column("ID", style="cyan")
    table.add_column("Event")
    table.add_column("Dataset")
    table.add_column("Actor")
    table.add_column("Timestamp")

    for entry in results:
        if isinstance(entry, dict):
            table.add_row(
                entry.get("audit_id", ""),
                entry.get("event_type", ""),
                entry.get("dataset_name", ""),
                entry.get("actor", ""),
                str(entry.get("timestamp", "")),
            )
        else:
            table.add_row(str(entry), "", "", "", "")

    console.print(table)


@audit_group.command("export")
@click.argument("dataset_name")
@click.option("--output", default=None, help="Output file path (default: stdout)")
@click.pass_context
def audit_export(ctx: click.Context, dataset_name: str, output: str | None) -> None:
    """Export audit trail for a dataset as JSON."""
    lake = _get_lake(ctx)

    try:
        data = lake.audit_export(dataset_name)
    except Exception as exc:
        _print_error(f"Export failed: {exc}")
        raise SystemExit(1) from None

    content = json.dumps(data, indent=2, default=str)

    if output:
        from pathlib import Path
        Path(output).write_text(content)
        _print_success(f"Audit trail exported to '{output}'")
    else:
        click.echo(content)


@audit_group.command("analyze")
@click.pass_context
def audit_analyze(ctx: click.Context) -> None:
    """Run anomaly detection on the audit trail."""
    lake = _get_lake(ctx)

    try:
        anomalies = lake.audit_analyze()
    except Exception as exc:
        _print_error(f"Anomaly detection failed: {exc}")
        raise SystemExit(1) from None

    if not anomalies:
        console.print("[dim]No anomalies detected.[/dim]")
        return

    table = Table(title=f"Anomalies ({len(anomalies)} found)")
    table.add_column("Type", style="cyan")
    table.add_column("Severity")
    table.add_column("Description")
    table.add_column("Events", justify="right")
    table.add_column("Detected At")

    for a in anomalies:
        if isinstance(a, dict):
            table.add_row(
                a.get("anomaly_type", ""),
                a.get("severity", ""),
                a.get("description", ""),
                str(a.get("affected_events", "")),
                str(a.get("detected_at", "")),
            )
        else:
            table.add_row(
                getattr(a, "anomaly_type", ""),
                getattr(a, "severity", ""),
                getattr(a, "description", ""),
                str(getattr(a, "affected_events", "")),
                str(getattr(a, "detected_at", "")),
            )

    console.print(table)
