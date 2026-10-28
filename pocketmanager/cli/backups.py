"""Backup commands for the PocketManager CLI."""

from __future__ import annotations

import os
import sys
from typing import Any

import click
from rich.panel import Panel
from rich.table import Table

from .common import (
    INSTANCE_NAME,
    _latest_backup_key,
    _require_backup_auth,
    _require_sftp_config,
    _upload_and_prune,
    cli,
    console,
)

# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name", type=INSTANCE_NAME)
@click.option("--download", is_flag=True, default=False, help="Download the backup after creation.")
@click.option("--name", "backup_name", default=None, help="Custom backup name.")
@click.option("--push", "push_remote", is_flag=True, default=False,
              help="Upload the backup to SFTP after creation.")
def backup(name: str, download: bool, backup_name: str | None, push_remote: bool) -> None:
    """Create a backup of a PocketBase instance."""
    from pocketmanager.core import backup as backup_mod
    from pocketmanager.core.state import get_instance

    instance = get_instance(name)
    if instance is None:
        console.print(f"[bold red]Error: Instance '{name}' not found.[/bold red]")
        sys.exit(1)

    port = instance.get("port")
    if not port:
        console.print(f"[bold red]Error: No port configured for instance '{name}'.[/bold red]")
        sys.exit(1)

    instance_url = f"http://localhost:{port}"
    console.print(f"[bold]Creating backup for '{name}'...[/bold]")

    token = _require_backup_auth(name, instance)
    if token is None:
        sys.exit(1)

    success, reason = backup_mod.create_backup_ex(instance_url, name=backup_name, auth_token=token)
    if not success:
        console.print(
            f"[bold red]Error: Failed to create backup for '{name}': {reason}[/bold red]"
        )
        sys.exit(1)

    console.print(f"[bold green]Backup created successfully for '{name}'.[/bold green]")

    if download:
        # Find the backup we just created
        all_backups = backup_mod.list_backups(instance_url, auth_token=token)
        if not all_backups:
            console.print("[bold yellow]Warning: Could not list backups for download.[/bold yellow]")
            return

        # Pick the latest backup, or match by name if given
        backup_key = _latest_backup_key(all_backups, backup_name)
        if not backup_key:
            console.print("[bold yellow]Warning: Could not identify backup key for download.[/bold yellow]")
            return

        # Download to the current directory — the instance directory is
        # owned by the `pocketbase` service user and not writable by us.
        dest_path = os.path.abspath(backup_key)

        console.print(f"[bold]Downloading backup to {dest_path}...[/bold]")
        dl_ok = backup_mod.download_backup(instance_url, backup_key, dest_path, auth_token=token)
        if dl_ok:
            console.print(f"[bold green]Backup downloaded to: {dest_path}[/bold green]")
        else:
            console.print(f"[bold red]Error: Failed to download backup.[/bold red]")
            sys.exit(1)

    if push_remote:
        _push_latest_backup(name, instance, instance_url, token, backup_name)


# ---------------------------------------------------------------------------
# backups (list)
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name", type=INSTANCE_NAME)
@click.option("--remote", is_flag=True, default=False,
              help="List backups stored on the remote SFTP server instead of locally.")
def backups(name: str, remote: bool) -> None:
    """List backups for a PocketBase instance."""
    from pocketmanager.core import backup as backup_mod
    from pocketmanager.core.state import get_instance

    instance = get_instance(name)
    if instance is None:
        console.print(f"[bold red]Error: Instance '{name}' not found.[/bold red]")
        sys.exit(1)

    port = instance.get("port")
    if not port:
        console.print(f"[bold red]Error: No port configured for instance '{name}'.[/bold red]")
        sys.exit(1)

    if remote:
        _list_remote_backups(name)
        return

    instance_url = f"http://localhost:{port}"
    token = _require_backup_auth(name, instance)
    if token is None:
        sys.exit(1)
    backup_list = backup_mod.list_backups(instance_url, auth_token=token)

    if not backup_list:
        console.print(f"[dim]No backups found for '{name}'.[/dim]")
        return

    table = Table(title=f"Backups for {name}")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Modified", style="dim")
    table.add_column("Size", justify="right")

    for entry in backup_list:
        key = entry.get("key", "")
        modified = entry.get("modified", "")
        size = entry.get("size", "")
        # Format size if numeric (bytes)
        if isinstance(size, (int, float)):
            size = f"{size / (1024 * 1024):.1f} MB"
        table.add_row(str(key), str(modified), str(size))

    console.print(table)


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name", type=INSTANCE_NAME)
@click.argument("backup_key")
@click.option("--yes", "assume_yes", is_flag=True, default=False,
              help="Skip the confirmation prompt (non-interactive use).")
def restore(name: str, backup_key: str, assume_yes: bool) -> None:
    """Restore a PocketBase instance from a backup."""
    from pocketmanager.core import backup as backup_mod
    from pocketmanager.core.state import get_instance

    instance = get_instance(name)
    if instance is None:
        console.print(f"[bold red]Error: Instance '{name}' not found.[/bold red]")
        sys.exit(1)

    port = instance.get("port")
    if not port:
        console.print(f"[bold red]Error: No port configured for instance '{name}'.[/bold red]")
        sys.exit(1)

    # Double confirmation
    if not assume_yes and not click.confirm(
        f"This will restore from backup '{backup_key}'. Current data will be replaced. Continue?",
        default=False,
    ):
        console.print("[dim]Restore cancelled.[/dim]")
        return

    instance_url = f"http://localhost:{port}"
    console.print(f"[bold yellow]Warning:[/bold yellow] Instance '{name}' will restart after restore.")

    token = _require_backup_auth(name, instance)
    if token is None:
        sys.exit(1)

    console.print(f"[bold]Restoring '{name}' from backup '{backup_key}'...[/bold]")
    success, reason = backup_mod.restore_backup_ex(instance_url, backup_key, auth_token=token)
    if not success:
        console.print(
            f"[bold red]Error: Failed to restore backup '{backup_key}' for '{name}': "
            f"{reason}[/bold red]"
        )
        sys.exit(1)

    console.print(f"[bold green]Backup '{backup_key}' restored successfully for '{name}'.[/bold green]")
    console.print("[bold yellow]The instance will restart automatically.[/bold yellow]")


# ---------------------------------------------------------------------------
# SFTP helpers
# ---------------------------------------------------------------------------


def _push_latest_backup(
    name: str,
    instance: dict[str, Any],
    instance_url: str,
    token: str,
    backup_name: str | None,
) -> None:
    """Upload the most recent backup to SFTP."""
    from pocketmanager.core import backup as backup_mod

    sftp_config = _require_sftp_config()
    if sftp_config is None:
        sys.exit(1)

    # Find the backup we just created
    all_backups = backup_mod.list_backups(instance_url, auth_token=token)
    if not all_backups:
        console.print("[bold yellow]Warning: Could not list backups for SFTP upload.[/bold yellow]")
        return

    backup_key = _latest_backup_key(all_backups, backup_name)
    if not backup_key:
        console.print("[bold yellow]Warning: Could not identify backup key for SFTP upload.[/bold yellow]")
        return

    instance_dir = instance.get("instance_dir", "")
    console.print(f"[bold]Uploading backup to SFTP server...[/bold]")

    ok, result, deleted_count, deleted_files = _upload_and_prune(
        backup_key, name, instance_dir, sftp_config, token,
    )
    if ok:
        console.print(f"[bold green]Backup uploaded to: {result}[/bold green]")
        if deleted_count > 0:
            console.print(
                f"[dim]Pruned {deleted_count} old remote backup(s): "
                + ", ".join(deleted_files)
                + "[/dim]"
            )
    else:
        console.print(f"[bold red]Error: SFTP upload failed: {result}[/bold red]")
        sys.exit(1)


def _list_remote_backups(name: str) -> None:
    """List backups stored on the remote SFTP server."""
    from datetime import datetime

    from pocketmanager.core.sftp import list_remote_backups as sftp_list

    sftp_config = _require_sftp_config()
    if sftp_config is None:
        sys.exit(1)

    console.print(f"[bold]Listing remote backups for '{name}'...[/bold]")
    ok, result = sftp_list(name, sftp_config)

    if not ok:
        console.print(f"[bold red]Error: {result}[/bold red]")
        sys.exit(1)

    entries: list[dict[str, Any]] = result  # type: ignore[assignment]
    if not entries:
        console.print(f"[dim]No remote backups found for '{name}'.[/dim]")
        return

    table = Table(title=f"Remote Backups for {name}")
    table.add_column("Filename", style="cyan", no_wrap=True)
    table.add_column("Modified", style="dim")
    table.add_column("Size", justify="right")

    for entry in entries:
        filename = entry.get("filename", "")
        mtime = entry.get("last_modified", 0)
        size = entry.get("size", 0)
        # Format timestamp
        if isinstance(mtime, (int, float)) and mtime > 0:
            modified = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        else:
            modified = str(mtime)
        # Format size
        if isinstance(size, (int, float)):
            size_str = f"{size / (1024 * 1024):.1f} MB"
        else:
            size_str = str(size)
        table.add_row(filename, modified, size_str)

    console.print(table)


# ---------------------------------------------------------------------------
# push-backup
# ---------------------------------------------------------------------------


@cli.command("push-backup")
@click.argument("name", type=INSTANCE_NAME)
@click.argument("backup_key", required=False, default=None)
def push_backup(name: str, backup_key: str | None) -> None:
    """Upload a backup to the remote SFTP server.

    If BACKUP_KEY is omitted, the most recent backup is uploaded.

    \b
    Examples:
      pm push-backup myapp
      pm push-backup myapp pb_backup_acme_20260530143000.zip
    """
    from pocketmanager.core import backup as backup_mod
    from pocketmanager.core.state import get_instance

    sftp_config = _require_sftp_config()
    if sftp_config is None:
        sys.exit(1)

    instance = get_instance(name)
    if instance is None:
        console.print(f"[bold red]Error: Instance '{name}' not found.[/bold red]")
        sys.exit(1)
    instance_dir = instance.get("instance_dir", "")
    port = instance.get("port")

    token = _require_backup_auth(name, instance)
    if token is None:
        sys.exit(1)

    instance_url = f"http://localhost:{port}"

    # Resolve backup key
    if not backup_key:
        all_backups = backup_mod.list_backups(instance_url, auth_token=token)
        if not all_backups:
            console.print(f"[bold red]Error: No backups found for '{name}'.[/bold red]")
            sys.exit(1)
        backup_key = _latest_backup_key(all_backups, None)

    if not backup_key:
        console.print("[bold red]Error: Could not determine backup key.[/bold red]")
        sys.exit(1)

    console.print(f"[bold]Uploading '{backup_key}' for '{name}' to SFTP...[/bold]")
    ok, result, deleted_count, deleted_files = _upload_and_prune(
        backup_key, name, instance_dir, sftp_config, token,
    )
    if ok:
        console.print(f"[bold green]Backup uploaded to: {result}[/bold green]")
        if deleted_count > 0:
            console.print(
                f"[dim]Pruned {deleted_count} old remote backup(s): "
                + ", ".join(deleted_files)
                + "[/dim]"
            )
    else:
        console.print(f"[bold red]Error: SFTP upload failed: {result}[/bold red]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# local-backup-schedule
# ---------------------------------------------------------------------------


@cli.command("local-backup-schedule")
@click.argument("name", type=INSTANCE_NAME)
@click.option("--enable/--disable", default=None,
              help="Enable or disable automatic local backups.")
@click.option("--schedule", "cron_expr", default=None,
              help="Cron expression (e.g. '0 3 * * *').")
@click.option("--max-keep", type=int, default=None,
              help="Maximum number of local backups to keep.")
def local_backup_schedule(
    name: str,
    enable: bool | None,
    cron_expr: str | None,
    max_keep: int | None,
) -> None:
    """Configure automatic local backups for an instance.

    Local backups are managed by PocketBase's built-in scheduler.
    They are stored inside the instance's pb_data/backups/ directory.

    \b
    Examples:
      pm local-backup-schedule myapp --enable --schedule '0 3 * * *' --max-keep 7
      pm local-backup-schedule myapp --disable
      pm local-backup-schedule myapp
    """
    from pocketmanager.core import backup as backup_mod
    from pocketmanager.core.state import get_instance, update_instance

    instance = get_instance(name)
    if instance is None:
        console.print(f"[bold red]Error: Instance '{name}' not found.[/bold red]")
        sys.exit(1)

    port = instance.get("port")
    if not port:
        console.print(f"[bold red]Error: No port configured for instance '{name}'.[/bold red]")
        sys.exit(1)

    has_any_option = any([
        enable is not None, cron_expr is not None, max_keep is not None,
    ])

    if not has_any_option:
        # Show current status — query PocketBase's actual settings
        # (requires superuser auth; fall back to PM-side flags when the
        # instance has no stored credentials or is unreachable)
        try:
            from pocketmanager.core.backup import (
                get_backup_cron_status,
                get_instance_auth_token,
            )

            cron_status = get_backup_cron_status(
                f"http://localhost:{port}",
                auth_token=get_instance_auth_token(name),
            )
            if cron_status["cron"]:
                status = "[bold green]enabled[/bold green]" if cron_status["enabled"] else "[dim]disabled[/dim]"
                schedule = cron_status["cron"]
                keep = cron_status["max_keep"]
            else:
                raise RuntimeError("no credentials or unreachable")
        except Exception:
            # Fallback to PM-side flags
            status = "[bold green]enabled[/bold green]" if instance.get("auto_backup", False) else "[dim]disabled[/dim]"
            schedule = instance.get("backup_cron", "")
            keep = instance.get("backup_max_keep", "")

        panel_lines = f"[bold]Status:[/bold]    {status}\n"
        panel_lines += f"[bold]Schedule:[/bold]  {schedule or '(not set)'}\n"
        panel_lines += f"[bold]Max keep:[/bold]  {keep or '(default)'}"
        console.print(Panel(panel_lines, title=f"Local Backup Schedule: {name}", border_style="cyan"))
        return

    # Apply changes
    token = _require_backup_auth(name, instance)
    if token is None:
        sys.exit(1)

    instance_url = f"http://localhost:{port}"

    # Determine effective values
    effective_enabled = enable if enable is not None else instance.get("auto_backup", False)
    effective_cron = cron_expr or instance.get("backup_cron", "0 3 * * *")
    effective_max = max_keep or instance.get("backup_max_keep", 7)

    if effective_enabled:
        # Configure PocketBase's internal backup scheduler
        ok = backup_mod.configure_auto_backup(
            instance_url, effective_cron, effective_max, auth_token=token,
        )
        if not ok:
            console.print("[bold red]Error: Failed to configure PocketBase backup schedule.[/bold red]")
            sys.exit(1)
        console.print(
            f"[bold green]Local backup schedule enabled for '{name}'.[/bold green]\n"
            f"  Schedule: {effective_cron}\n"
            f"  Max keep: {effective_max}"
        )
    else:
        # Disable by setting cron to empty
        ok = backup_mod.configure_auto_backup(
            instance_url, "", 0, auth_token=token,
        )
        if not ok:
            console.print("[bold red]Error: Failed to disable PocketBase backup schedule.[/bold red]")
            sys.exit(1)
        console.print(f"[bold green]Local backup schedule disabled for '{name}'.[/bold green]")

    # Persist to instance state
    updates: dict[str, Any] = {
        "auto_backup": effective_enabled,
        "backup_cron": effective_cron if effective_enabled else "",
        "backup_max_keep": effective_max if effective_enabled else 0,
    }
    update_instance(name, updates)


# ---------------------------------------------------------------------------
# backup-all
# ---------------------------------------------------------------------------


@cli.command("backup-all")
@click.option("--push", "push_remote", is_flag=True, default=False,
              help="Upload each backup to SFTP after creation.")
def backup_all(push_remote: bool) -> None:
    """Create a backup of all PocketBase instances.

    Iterates every registered instance, creates a backup, and optionally
    pushes it to the configured SFTP server.

    Automatically run by the ``pm-sftp-backup`` systemd timer when
    SFTP backup scheduling is enabled (``pm sftp-backup-schedule --enable``).
    """
    from pocketmanager.core import backup as backup_mod
    from pocketmanager.core.state import get_all_instances

    instances = get_all_instances()
    if not instances:
        console.print("[dim]No instances found.[/dim]")
        return

    sftp_config = None
    if push_remote:
        sftp_config = _require_sftp_config()
        if sftp_config is None:
            sys.exit(1)

    table = Table(title="Backup All Instances")
    table.add_column("Instance", style="cyan")
    table.add_column("Backup", no_wrap=True)
    table.add_column("Status", justify="center")
    if push_remote:
        table.add_column("SFTP", justify="center")

    failures = 0

    for inst in instances:
        inst_name = inst.get("name", "")
        port = inst.get("port")
        if not port:
            failures += 1
            row = [inst_name, "-", "[bold red]no port[/bold red]"]
            if push_remote:
                row.append("-")
            table.add_row(*row)
            continue

        instance_url = f"http://localhost:{port}"
        token = _require_backup_auth(inst_name, inst)

        if token is None:
            failures += 1
            row = [inst_name, "-", "[bold red]no credentials[/bold red]"]
            if push_remote:
                row.append("-")
            table.add_row(*row)
            continue

        # Create backup
        ok, reason = backup_mod.create_backup_ex(instance_url, auth_token=token)
        if not ok:
            failures += 1
            row = [inst_name, "-", f"[bold red]failed ({reason})[/bold red]"]
            if push_remote:
                row.append("-")
            table.add_row(*row)
            continue

        # Find the backup we just created
        all_backups = backup_mod.list_backups(instance_url, auth_token=token)
        backup_key = _latest_backup_key(all_backups, None) or "unknown"

        row = [inst_name, backup_key, "[bold green]ok[/bold green]"]

        if push_remote and sftp_config:
            instance_dir = inst.get("instance_dir", "")
            ok_sftp, result, deleted_count, _ = _upload_and_prune(
                backup_key, inst_name, instance_dir, sftp_config, token,
            )
            if ok_sftp:
                row.append("[bold green]uploaded[/bold green]")
                if deleted_count:
                    row[-1] = f"[bold green]uploaded[/bold green] [dim]({deleted_count} pruned)[/dim]"
            else:
                failures += 1
                row.append(f"[bold red]{result}[/bold red]")

        table.add_row(*row)

    console.print(table)

    # Non-zero exit on any failure so the pm-sftp-backup timer (and scripts)
    # can detect partial failures via the service result.
    if failures:
        console.print(
            f"[bold red]{failures} instance(s) failed to back up.[/bold red]"
        )
        sys.exit(1)
