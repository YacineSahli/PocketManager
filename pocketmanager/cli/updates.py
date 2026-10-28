"""Update and migration commands for the PocketManager CLI."""

from __future__ import annotations

import sys

import click
from rich.table import Table

from .common import INSTANCE_NAME, cli, console

# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name", type=INSTANCE_NAME)
@click.option("--version", "pb_version", default=None, help="Target PocketBase version.")
def update(name: str, pb_version: str | None) -> None:
    """Update a PocketBase instance to a new version."""
    from pocketmanager.core import instance as instance_mod
    from pocketmanager.core.state import get_instance

    instance = get_instance(name)
    if instance is None:
        console.print(f"[bold red]Error: Instance '{name}' not found.[/bold red]")
        sys.exit(1)

    old_version = instance.get("version", "unknown")

    try:
        result = instance_mod.update_instance(name, version=pb_version)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        sys.exit(1)

    new_version = result.get("version", pb_version or "latest")
    if result.get("skipped"):
        console.print(
            f"[dim]Instance '{name}' is already at version {new_version} — nothing to do.[/dim]"
        )
        return
    console.print(
        f"[bold green]Instance '{name}' updated: {old_version} → {new_version}[/bold green]"
    )


# ---------------------------------------------------------------------------
# update-all
# ---------------------------------------------------------------------------


@cli.command("update-all")
@click.option("--yes", "assume_yes", is_flag=True, default=False,
              help="Skip the confirmation prompt (non-interactive use).")
def update_all(assume_yes: bool) -> None:
    """Update all PocketBase instances to the latest version.

    Asks for confirmation first (restarts cause downtime) and skips
    instances already at the target version.  Exits non-zero if any
    update fails.
    """
    from pocketmanager.core import instance as instance_mod
    from pocketmanager.core.state import get_all_instances

    instances = get_all_instances()
    if not instances:
        console.print("[dim]No instances found.[/dim]")
        return

    if not assume_yes:
        console.print(
            f"[bold]This will update {len(instances)} instance(s) to the latest "
            "PocketBase version. Each update restarts the instance (brief downtime).[/bold]"
        )
        if not click.confirm("Continue?", default=False):
            console.print("[dim]Update cancelled.[/dim]")
            return

    table = Table(title="Update All Instances")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Old Version")
    table.add_column("New Version")
    table.add_column("Status", justify="center")

    failures = 0
    for inst in instances:
        inst_name = inst.get("name", "")
        old_version = inst.get("version", "unknown")
        try:
            result = instance_mod.update_instance(inst_name)
            new_version = result.get("version", "latest")
            if result.get("skipped"):
                table.add_row(inst_name, old_version, new_version, "[dim]already current[/dim]")
            else:
                table.add_row(inst_name, old_version, new_version, "[bold green]ok[/bold green]")
        except (ValueError, RuntimeError) as exc:
            failures += 1
            table.add_row(inst_name, old_version, "-", f"[bold red]{exc}[/bold red]")

    console.print(table)
    if failures:
        console.print(f"[bold red]{failures} update(s) failed.[/bold red]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# self-update
# ---------------------------------------------------------------------------


@cli.command("self-update")
@click.option("--yes", "assume_yes", is_flag=True, default=False,
              help="Skip the confirmation prompt (non-interactive use).")
def self_update(assume_yes: bool) -> None:
    """Update PocketManager to the latest version."""
    from pocketmanager.core import selfupdate as selfupdate_mod

    console.print("[bold]Checking for updates...[/bold]")
    update = selfupdate_mod.check_for_update()

    if update is None:
        if not selfupdate_mod.has_version_marker():
            console.print(
                "[bold yellow]This installation has no version marker — "
                "self-update cannot compare versions.[/bold yellow]\n"
                "[dim]Reinstall once from the repo to enable self-updating:\n"
                "  pip install --user --force-reinstall --no-cache-dir "
                "git+https://github.com/YacineSahli/PocketManager.git@master[/dim]"
            )
        else:
            console.print(
                "[bold yellow]Could not check for updates[/bold yellow] "
                "(GitHub unreachable or rate limited — try again later)."
            )
        return

    if update.get("up_to_date"):
        console.print("[bold green]PocketManager is already up to date.[/bold green]")
        return

    console.print(f"\n[bold]Update available:[/bold]")
    console.print(f"  [cyan]Local:[/cyan]  {update['local']}")
    console.print(f"  [cyan]Remote:[/cyan] {update['remote']}")

    incoming = update.get("log", "")
    if incoming:
        console.print(f"\n[bold]Incoming commits:[/bold]")
        for line in incoming.splitlines():
            console.print(f"  {line}")

    if not assume_yes and not click.confirm("\nDo you want to update now?", default=True):
        console.print("[dim]Update cancelled.[/dim]")
        return

    console.print()
    success = selfupdate_mod.perform_update()
    if success:
        console.print("\n[bold green]PocketManager updated successfully.[/bold green]")
        console.print(
            "[bold]Run 'pm doctor' to apply post-update migrations:[/bold] "
            "rebinds instances to the configured bind_host (important for "
            "pre-2026-10-28 installs bound to 0.0.0.0) and removes the "
            "legacy root-crontab SFTP job."
        )
    else:
        console.print("[bold red]Update failed. Check the logs for details.[/bold red]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# migrate-existing
# ---------------------------------------------------------------------------


@cli.command("migrate-existing")
def migrate_existing() -> None:
    """Detect and import manually-created PocketBase instances."""
    from pocketmanager.core import instance as instance_mod

    console.print("[bold]Scanning for existing PocketBase instances...[/bold]")
    migrated = instance_mod.migrate_existing()

    if not migrated:
        console.print("[dim]No new instances found to migrate.[/dim]")
        return

    console.print(f"[bold green]Migrated {len(migrated)} instance(s):[/bold green]")
    for inst in migrated:
        email = inst.get("superadmin_email")
        suffix = f", superadmin provisioned: {email}" if email else ""
        console.print(
            f"  [cyan]•[/cyan] {inst.get('name', 'unknown')} "
            f"(port {inst.get('port', '?')}{suffix})"
        )
