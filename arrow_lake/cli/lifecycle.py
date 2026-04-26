"""CLI lifecycle commands — blob tiering, Glacier restore, cost estimation."""

from __future__ import annotations

import click
from rich.table import Table

from arrow_lake.cli import _get_lake, _print_error, _print_success, _print_warning, console


@click.group()
def lifecycle_group() -> None:
    """Blob lifecycle management — tiering, restore, cost estimation."""


@lifecycle_group.command("apply")
@click.option("--prefix", default="", help="S3 key prefix for lifecycle rules")
@click.pass_context
def lifecycle_apply(ctx: click.Context, prefix: str) -> None:
    """Apply lifecycle rules to the S3 bucket/prefix."""
    lake = _get_lake(ctx)

    try:
        result = lake.lifecycle_apply(prefix=prefix)
    except Exception as exc:
        _print_error(f"Lifecycle apply failed: {exc}")
        raise SystemExit(1) from None

    status = result.get("status", "unknown")
    if status == "disabled":
        _print_warning("Lifecycle is disabled in config")
    elif status == "applied":
        _print_success(f"Applied {result.get('rules_applied', 0)} lifecycle rule(s)")
    else:
        console.print(f"Status: {status}")


@lifecycle_group.command("status")
@click.option("--prefix", default="", help="S3 key prefix to scan")
@click.pass_context
def lifecycle_status(ctx: click.Context, prefix: str) -> None:
    """Show storage tier for objects under a prefix."""
    lake = _get_lake(ctx)

    try:
        objects = lake.lifecycle_status(prefix=prefix)
    except Exception as exc:
        _print_error(f"Failed to get lifecycle status: {exc}")
        raise SystemExit(1) from None

    if not objects:
        console.print("[dim]No objects found.[/dim]")
        return

    table = Table(title=f"Storage Tiers: {prefix or '(root)'}")
    table.add_column("Key", style="cyan")
    table.add_column("Tier")
    table.add_column("Size", justify="right")

    for obj in objects:
        table.add_row(obj.get("key", ""), obj.get("tier", ""), obj.get("size", ""))

    console.print(table)


@lifecycle_group.command("restore")
@click.argument("key")
@click.option("--days", default=7, type=int, help="Days to keep restored copy")
@click.pass_context
def lifecycle_restore(ctx: click.Context, key: str, days: int) -> None:
    """Restore a Glacier-tiered object for temporary access."""
    lake = _get_lake(ctx)

    console.print(f"[dim]Initiating Glacier restore for '{key}' ({days} days)...[/dim]")

    try:
        result = lake.lifecycle_restore(key, days=days)
    except Exception as exc:
        _print_error(f"Glacier restore failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Restore initiated: {result.get('tier', 'Standard')} retrieval, {days} days")


@lifecycle_group.command("estimate")
@click.option("--size-gb", required=True, type=int, help="Total data size in GB")
@click.option("--target-tier", default="STANDARD_IA", type=click.Choice(["STANDARD_IA", "GLACIER", "DEEP_ARCHIVE"]))
@click.pass_context
def lifecycle_estimate(ctx: click.Context, size_gb: int, target_tier: str) -> None:
    """Estimate monthly cost savings from tier transition."""
    lake = _get_lake(ctx)

    try:
        result = lake.lifecycle_estimate(size_gb, target_tier)
    except Exception as exc:
        _print_error(f"Cost estimation failed: {exc}")
        raise SystemExit(1) from None

    table = Table(title="Cost Estimation")
    table.add_column("Metric", style="cyan")
    table.add_column("Value")
    table.add_row("Total size", f"{result['total_size_gb']} GB")
    table.add_row("Current tier", result["current_tier"])
    table.add_row("Current monthly", f"${result['current_monthly_cost']}")
    table.add_row("Target tier", result["target_tier"])
    table.add_row("Target monthly", f"${result['target_monthly_cost']}")
    table.add_row("Monthly savings", f"${result['monthly_savings']} ({result['savings_percent']}%)")
    console.print(table)


@lifecycle_group.command("rules")
@click.option("--prefix", default="", help="S3 key prefix")
@click.pass_context
def lifecycle_rules(ctx: click.Context, prefix: str) -> None:
    """Preview lifecycle rules without applying."""
    lake = _get_lake(ctx)

    try:
        result = lake.lifecycle_rules(prefix=prefix)
    except Exception as exc:
        _print_error(f"Failed to get rules: {exc}")
        raise SystemExit(1) from None

    if not result.get("enabled"):
        console.print("[yellow]Lifecycle is disabled in config.[/yellow]")
        return

    table = Table(title="Lifecycle Rules Preview")
    table.add_column("Setting", style="cyan")
    table.add_column("Value")
    table.add_row("Prefix", result.get("prefix", ""))
    table.add_row("Standard → IA", f"{result['standard_to_ia_days']} days")
    table.add_row("IA → Glacier", f"{result['ia_to_glacier_days']} days")
    table.add_row("Glacier → Expiration", f"{result['glacier_expiration_days']} days")
    table.add_row("Excluded", ", ".join(result.get("excluded_prefixes", [])))
    console.print(table)

    rules = result.get("rules", [])
    if rules:
        for rule in rules:
            rule_id = rule.get("ID", "")
            transitions = rule.get("Transitions", [])
            expiration = rule.get("Expiration", {})
            desc = ""
            if transitions:
                for t in transitions:
                    desc += f"→ {t.get('StorageClass')} after {t.get('Days')}d  "
            if expiration:
                desc += f"expires after {expiration.get('Days')}d"
            console.print(f"  [dim]{rule_id}[/dim]: {desc.strip()}")


@lifecycle_group.command("config")
@click.pass_context
def lifecycle_config(ctx: click.Context) -> None:
    """Show current lifecycle configuration."""
    lake = _get_lake(ctx)
    lc = lake._config.lifecycle

    table = Table(title="Lifecycle Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value")
    table.add_row("Enabled", str(lc.enabled))
    table.add_row("Standard → IA", f"{lc.standard_to_ia_days} days")
    table.add_row("IA → Glacier", f"{lc.ia_to_glacier_days} days")
    table.add_row("Glacier expiration", f"{lc.glacier_expiration_days} days")
    table.add_row("Glacier retrieval tier", lc.glacier_retrieval_tier)
    table.add_row("Excluded prefixes", ", ".join(lc.excluded_prefixes))
    console.print(table)
