"""CLI backup commands — create, list, restore, delete backups."""

from __future__ import annotations

import click
from rich.table import Table

from arrow_lake.cli import _get_lake, _print_error, _print_success, console


@click.group()
def backup_group() -> None:
    """Manage backups (create, list, restore, delete)."""


@backup_group.command("create")
@click.option("--datasets", multiple=True, help="Datasets to include")
@click.option("--backup-id", default=None, help="Custom backup ID")
@click.pass_context
def backup_create(ctx: click.Context, datasets: tuple[str, ...], backup_id: str | None) -> None:
    """Create a backup of datasets."""
    lake = _get_lake(ctx)

    ds_list = list(datasets) if datasets else None
    console.print("[dim]Creating backup...[/dim]")

    try:
        from arrow_lake.ops.backup import BackupManager
        bm = BackupManager(storage=lake._storage)
        info = bm.create_backup(dataset_names=ds_list, backup_id=backup_id)
    except Exception as exc:
        _print_error(f"Backup creation failed: {exc}")
        raise SystemExit(1) from None

    console.print(f"  Backup ID: {info.backup_id}")
    console.print(f"  Created: {info.created_at}")
    console.print(f"  Size: {info.total_size}")
    _print_success("Backup created")


@backup_group.command("list")
@click.pass_context
def backup_list(ctx: click.Context) -> None:
    """List all available backups."""
    lake = _get_lake(ctx)

    try:
        from arrow_lake.ops.backup import BackupManager
        bm = BackupManager(storage=lake._storage)
        backups = bm.list_backups()
    except Exception as exc:
        _print_error(f"Failed to list backups: {exc}")
        raise SystemExit(1) from None

    if not backups:
        console.print("[dim]No backups found.[/dim]")
        return

    table = Table(title="Backups")
    table.add_column("Backup ID", style="cyan")
    table.add_column("Created")
    table.add_column("Datasets")
    table.add_column("Size")

    for b in backups:
        ds_str = ", ".join(getattr(b, "dataset_names", [])) or "—"
        table.add_row(
            getattr(b, "backup_id", "—"),
            str(getattr(b, "created_at", "—")),
            ds_str,
            str(getattr(b, "total_size", "—")),
        )

    console.print(table)


@backup_group.command("restore")
@click.argument("backup_id")
@click.option("--datasets", multiple=True, help="Specific datasets to restore")
@click.pass_context
def backup_restore(ctx: click.Context, backup_id: str, datasets: tuple[str, ...]) -> None:
    """Restore a backup."""
    lake = _get_lake(ctx)

    ds_list = list(datasets) if datasets else None
    console.print(f"[dim]Restoring backup {backup_id}...[/dim]")

    try:
        from arrow_lake.ops.backup import BackupManager
        bm = BackupManager(storage=lake._storage)
        bm.restore_backup(backup_id, dataset_names=ds_list)
    except Exception as exc:
        _print_error(f"Backup restore failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Backup '{backup_id}' restored")


@backup_group.command("delete")
@click.argument("backup_id")
@click.pass_context
def backup_delete(ctx: click.Context, backup_id: str) -> None:
    """Delete a backup."""
    lake = _get_lake(ctx)

    if not click.confirm(f"Delete backup '{backup_id}'?"):
        return

    try:
        from arrow_lake.ops.backup import BackupManager
        bm = BackupManager(storage=lake._storage)
        bm.delete_backup(backup_id)
    except Exception as exc:
        _print_error(f"Backup deletion failed: {exc}")
        raise SystemExit(1) from None

    _print_success(f"Backup '{backup_id}' deleted")
