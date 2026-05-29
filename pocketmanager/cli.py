"""PocketManager CLI — manage multiple PocketBase instances on a single VPS."""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pocketmanager import __version__

# Module-level console for all output
console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_url(instance: dict) -> str:
    """Build the public URL for an instance from its domain/subdomain config."""
    domain = instance.get("domain")
    subdomain = instance.get("subdomain")
    port = instance.get("port")
    name = instance.get("name", "")

    if domain:
        return f"https://{domain}"
    if subdomain:
        # Default base domain from pangolin config
        try:
            from pocketmanager.core.config import get as _cfg_get

            base = _cfg_get("pangolin.default_domain", "")
            if base:
                return f"https://{subdomain}.{base}"
        except Exception:
            pass
        return f"https://{subdomain}.example.com"
    if port:
        return f"http://localhost:{port}"
    return "(unknown)"


def _format_uptime(seconds: float | None) -> str:
    """Format uptime seconds into a human-readable string."""
    if seconds is None:
        return "N/A"
    try:
        delta = timedelta(seconds=int(seconds))
        parts = []
        if delta.days:
            parts.append(f"{delta.days}d")
        hours, remainder = divmod(delta.seconds, 3600)
        if hours:
            parts.append(f"{hours}h")
        minutes, secs = divmod(remainder, 60)
        if minutes:
            parts.append(f"{minutes}m")
        if secs and not parts:
            parts.append(f"{secs}s")
        return " ".join(parts) if parts else "0s"
    except (TypeError, ValueError):
        return "N/A"


def _status_style(active: bool) -> str:
    """Return rich markup for an active/inactive status."""
    return "[bold green]running[/bold green]" if active else "[bold red]stopped[/bold red]"


# ---------------------------------------------------------------------------
# CLI Group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version=__version__, prog_name="pocketmanager")
def cli() -> None:
    """PocketManager — manage multiple PocketBase instances on a single VPS."""


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name")
@click.option("-p", "--port", type=int, default=None, help="HTTP port for the instance.")
@click.option("-d", "--domain", default=None, help="Custom domain (e.g. api.example.com).")
@click.option("-s", "--subdomain", default=None, help="Subdomain label.")
@click.option(
    "-e",
    "--env",
    multiple=True,
    help="Environment variable in KEY=VAL format (repeatable).",
)
@click.option("--version", "pb_version", default=None, help="PocketBase version to install.")
@click.option("--no-pangolin", is_flag=True, default=False, help="Skip pangolin integration.")
def create(
    name: str,
    port: int | None,
    domain: str | None,
    subdomain: str | None,
    env: tuple[str, ...],
    pb_version: str | None,
    no_pangolin: bool,
) -> None:
    """Create a new PocketBase instance."""
    from pocketmanager.core import instance as instance_mod

    # Interactive mode: no options provided
    interactive = port is None and domain is None and subdomain is None and not env and pb_version is None

    env_dict: dict[str, str] = {}
    for item in env:
        if "=" not in item:
            console.print(f"[bold red]Error:[/bold red] Invalid env format '{item}'. Use KEY=VAL.")
            sys.exit(1)
        key, _, value = item.partition("=")
        env_dict[key] = value

    if interactive:
        console.print(f"[bold]Creating new instance: {name}[/bold]\n")

        # Auto-detect port
        try:
            from pocketmanager.core.ports import find_available_port

            suggested_port = find_available_port()
        except Exception:
            suggested_port = 8090

        port = click.prompt("HTTP port", type=int, default=suggested_port)
        domain_str: str = click.prompt("Domain (leave blank for none)", type=str, default="")
        domain = domain_str.strip() or None
        subdomain_str: str = click.prompt("Subdomain (leave blank for none)", type=str, default="")
        subdomain = subdomain_str.strip() or None
        if click.confirm("Add environment variables?", default=False):
            while True:
                key = click.prompt("Variable name (leave blank to finish)", type=str, default="")
                key = key.strip()
                if not key:
                    break
                value = click.prompt(f"Value for {key}", type=str, default="")
                env_dict[key] = value

    try:
        result = instance_mod.create_instance(
            name=name,
            port=port,
            subdomain=subdomain,
            domain=domain,
            env=env_dict or None,
            version=pb_version,
            pangolin=not no_pangolin,
        )
    except (ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        sys.exit(1)

    url = _build_url(result)
    panel_content = (
        f"[bold]Name:[/bold]     {result.get('name', name)}\n"
        f"[bold]Port:[/bold]     {result.get('port', port)}\n"
        f"[bold]URL:[/bold]      {url}\n"
        f"[bold]Admin:[/bold]    {url}/_/\n"
        f"[bold]Version:[/bold]  {result.get('version', pb_version or 'latest')}"
    )
    console.print(Panel(panel_content, title="[bold green]Instance Created[/bold green]", border_style="green"))


# ---------------------------------------------------------------------------
# list / ls
# ---------------------------------------------------------------------------


@cli.command("list")
def list_instances_cmd() -> None:
    """List all PocketBase instances."""
    from pocketmanager.core import instance as instance_mod

    instances = instance_mod.list_instances()

    if not instances:
        console.print("[dim]No instances found.[/dim]")
        return

    table = Table(title="PocketBase Instances")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Port", justify="right", style="magenta")
    table.add_column("Status", justify="center")
    table.add_column("Version", style="dim")
    table.add_column("URL")

    for inst in instances:
        active = inst.get("active", False)
        table.add_row(
            inst.get("name", ""),
            str(inst.get("port", "")),
            _status_style(active),
            inst.get("version", ""),
            _build_url(inst),
        )

    console.print(table)


cli.add_command(list_instances_cmd, "ls")


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name")
def start(name: str) -> None:
    """Start a PocketBase instance."""
    from pocketmanager.core import instance as instance_mod

    try:
        success = instance_mod.start_instance(name)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        sys.exit(1)

    if success:
        console.print(f"[bold green]Instance '{name}' started successfully.[/bold green]")
    else:
        console.print(f"[bold red]Failed to start instance '{name}'.[/bold red]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name")
def stop(name: str) -> None:
    """Stop a PocketBase instance."""
    from pocketmanager.core import instance as instance_mod

    try:
        success = instance_mod.stop_instance(name)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        sys.exit(1)

    if success:
        console.print(f"[bold green]Instance '{name}' stopped successfully.[/bold green]")
    else:
        console.print(f"[bold red]Failed to stop instance '{name}'.[/bold red]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# restart
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name")
def restart(name: str) -> None:
    """Restart a PocketBase instance."""
    from pocketmanager.core import instance as instance_mod

    try:
        success = instance_mod.restart_instance(name)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        sys.exit(1)

    if success:
        console.print(f"[bold green]Instance '{name}' restarted successfully.[/bold green]")
    else:
        console.print(f"[bold red]Failed to restart instance '{name}'.[/bold red]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name")
@click.option("--keep-data", is_flag=True, default=False, help="Keep instance data on disk.")
@click.option("--force", is_flag=True, default=False, help="Skip confirmation prompts.")
def remove(name: str, keep_data: bool, force: bool) -> None:
    """Remove a PocketBase instance."""
    from pocketmanager.core import instance as instance_mod

    # Fetch instance info for the confirmation flow
    try:
        info = instance_mod.get_instance_info(name)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        sys.exit(1)

    if not force:
        # 1. Print instance info
        panel_content = (
            f"[bold]Name:[/bold]        {info.get('name', name)}\n"
            f"[bold]Port:[/bold]        {info.get('port', '')}\n"
            f"[bold]URL:[/bold]         {_build_url(info)}\n"
            f"[bold]Disk usage:[/bold]  {info.get('disk_usage', 'unknown')}"
        )
        console.print(Panel(panel_content, title=f"Instance: {name}", border_style="red"))

        # 2. Warning
        console.print("\n[bold yellow]WARNING: This action is irreversible![/bold yellow]")
        if not keep_data:
            console.print("[bold yellow]All instance data will be permanently deleted.[/bold yellow]")

        # 3. Backup confirmation
        if not click.confirm("Have you backed up your data?", default=False):
            console.print("[dim]Removal cancelled.[/dim]")
            return

        # 4. Name confirmation
        confirmation = click.prompt(
            f'Type the instance name "{name}" to confirm deletion',
            type=str,
        )
        if confirmation != name:
            console.print("[bold red]Confirmation mismatch. Removal cancelled.[/bold red]")
            return

    # Perform removal
    console.print(f"\n[bold]Removing instance '{name}'...[/bold]")
    console.print("  [dim]→ Stopping service...[/dim]")
    try:
        instance_mod.remove_instance(name, keep_data=keep_data)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        sys.exit(1)

    console.print("  [dim]→ Removing systemd service...[/dim]")
    if keep_data:
        console.print("  [dim]→ Keeping data on disk (--keep-data).[/dim]")
    else:
        console.print("  [dim]→ Deleting instance directory...[/dim]")
    console.print("  [dim]→ Updating state...[/dim]")

    console.print(f"\n[bold green]Instance '{name}' removed successfully.[/bold green]")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name")
def status(name: str) -> None:
    """Show detailed status for a PocketBase instance."""
    from pocketmanager.core import instance as instance_mod

    try:
        info = instance_mod.get_instance_info(name)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        sys.exit(1)

    active = info.get("active", False)
    health = info.get("health", False)
    env_vars = info.get("env", {})

    panel_content = (
        f"[bold]Name:[/bold]         {info.get('name', name)}\n"
        f"[bold]Status:[/bold]       {_status_style(active)}\n"
        f"[bold]Port:[/bold]         {info.get('port', '')}\n"
        f"[bold]URL:[/bold]          {_build_url(info)}\n"
        f"[bold]Version:[/bold]      {info.get('version', '')}\n"
        f"[bold]Disk usage:[/bold]   {info.get('disk_usage', 'unknown')}\n"
        f"[bold]Health:[/bold]       {'[bold green]healthy[/bold green]' if health else '[bold red]unhealthy[/bold red]'}\n"
        f"[bold]Uptime:[/bold]       {_format_uptime(info.get('uptime_seconds'))}\n"
        f"[bold]Backups:[/bold]      {info.get('backup_count', 0)}"
    )

    if env_vars:
        env_lines = "\n".join(f"  [dim]{k}[/dim]={v}" for k, v in env_vars.items())
        panel_content += f"\n[bold]Environment:[/bold]\n{env_lines}"

    console.print(Panel(panel_content, title=f"Instance: {name}", border_style="cyan"))


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name")
@click.option("-n", "--lines", default=100, help="Number of log lines to show.")
@click.option("-f", "--follow", is_flag=True, default=False, help="Follow log output.")
def logs(name: str, lines: int, follow: bool) -> None:
    """Show logs for a PocketBase instance."""
    from pocketmanager.core.systemd import get_service_name

    service_name = get_service_name(name)

    if follow:
        # Exec journalctl directly for proper streaming
        try:
            os.execvp("journalctl", ["journalctl", "-u", service_name, "-f", "--no-pager"])
        except OSError as exc:
            console.print(f"[bold red]Error: Could not exec journalctl: {exc}[/bold red]")
            sys.exit(1)
    else:
        from pocketmanager.core.systemd import get_journal_logs

        output = get_journal_logs(name, lines=lines, follow=False)
        if output:
            console.print(output.rstrip(), highlight=False)
        else:
            console.print(f"[dim]No logs found for '{name}'.[/dim]")


# ---------------------------------------------------------------------------
# healthcheck
# ---------------------------------------------------------------------------


@cli.command("healthcheck")
def healthcheck_cmd() -> None:
    """Check health of all PocketBase instances."""
    from pocketmanager.core import health as health_mod

    results = health_mod.check_all_instances()

    if not results:
        console.print("[dim]No instances found.[/dim]")
        return

    # Use rich table directly for consistent formatting
    table = Table(title="Instance Health")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Port", justify="right", style="magenta")
    table.add_column("Active", justify="center")
    table.add_column("Healthy", justify="center")
    table.add_column("Response (ms)", justify="right")
    table.add_column("Error")

    healthy_count = 0
    for row in results:
        active_str = "[green]yes[/green]" if row["active"] else "[dim]no[/dim]"
        healthy_str = "[green]yes[/green]" if row["healthy"] else "[red]no[/red]"
        resp_str = str(row["response_time_ms"]) if row["response_time_ms"] is not None else "-"
        error_str = row.get("error") or ""

        if row["healthy"]:
            healthy_count += 1

        table.add_row(
            row["name"],
            str(row["port"]),
            active_str,
            healthy_str,
            resp_str,
            error_str,
        )

    console.print(table)

    total = len(results)
    if healthy_count == total:
        console.print(f"\n[bold green]All {total} instance(s) healthy.[/bold green]")
    else:
        unhealthy = total - healthy_count
        console.print(
            f"\n[bold yellow]{healthy_count} of {total} instance(s) healthy, "
            f"{unhealthy} have issues.[/bold yellow]"
        )


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name")
@click.option("--download", is_flag=True, default=False, help="Download the backup after creation.")
@click.option("--name", "backup_name", default=None, help="Custom backup name.")
def backup(name: str, download: bool, backup_name: str | None) -> None:
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

    success = backup_mod.create_backup(instance_url, name=backup_name, auth_token=token)
    if not success:
        console.print(f"[bold red]Error: Failed to create backup for '{name}'.[/bold red]")
        sys.exit(1)

    console.print(f"[bold green]Backup created successfully for '{name}'.[/bold green]")

    if download:
        # Find the backup we just created
        all_backups = backup_mod.list_backups(instance_url, auth_token=token)
        if not all_backups:
            console.print("[bold yellow]Warning: Could not list backups for download.[/bold yellow]")
            return

        # Pick the latest backup, or match by name if given
        target = None
        if backup_name:
            for b in all_backups:
                if b.get("key", "").startswith(backup_name):
                    target = b
                    break
        if not target:
            # Sort by modified descending and take the most recent
            all_backups.sort(key=lambda b: b.get("modified", ""), reverse=True)
            target = all_backups[0]

        backup_key = target.get("key", "")
        if not backup_key:
            console.print("[bold yellow]Warning: Could not identify backup key for download.[/bold yellow]")
            return

        # Download to the instance directory
        instance_dir = instance.get("instance_dir", "")
        dest_path = f"{instance_dir}/{backup_key}" if instance_dir else backup_key

        console.print(f"[bold]Downloading backup to {dest_path}...[/bold]")
        dl_ok = backup_mod.download_backup(instance_url, backup_key, dest_path, auth_token=token)
        if dl_ok:
            console.print(f"[bold green]Backup downloaded to: {dest_path}[/bold green]")
        else:
            console.print(f"[bold red]Error: Failed to download backup.[/bold red]")
            sys.exit(1)


# ---------------------------------------------------------------------------
# backups (list)
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name")
def backups(name: str) -> None:
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
@click.argument("name")
@click.argument("backup_key")
def restore(name: str, backup_key: str) -> None:
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
    if not click.confirm(
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
    success = backup_mod.restore_backup(instance_url, backup_key, auth_token=token)
    if not success:
        console.print(f"[bold red]Error: Failed to restore backup '{backup_key}' for '{name}'.[/bold red]")
        sys.exit(1)

    console.print(f"[bold green]Backup '{backup_key}' restored successfully for '{name}'.[/bold green]")
    console.print("[bold yellow]The instance will restart automatically.[/bold yellow]")


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name")
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
    console.print(
        f"[bold green]Instance '{name}' updated: {old_version} → {new_version}[/bold green]"
    )


# ---------------------------------------------------------------------------
# update-all
# ---------------------------------------------------------------------------


@cli.command("update-all")
def update_all() -> None:
    """Update all PocketBase instances to the latest version."""
    from pocketmanager.core import instance as instance_mod
    from pocketmanager.core.state import get_all_instances

    instances = get_all_instances()
    if not instances:
        console.print("[dim]No instances found.[/dim]")
        return

    table = Table(title="Update All Instances")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Old Version")
    table.add_column("New Version")
    table.add_column("Status", justify="center")

    for inst in instances:
        inst_name = inst.get("name", "")
        old_version = inst.get("version", "unknown")
        try:
            result = instance_mod.update_instance(inst_name)
            new_version = result.get("version", "latest")
            table.add_row(inst_name, old_version, new_version, "[bold green]ok[/bold green]")
        except (ValueError, RuntimeError) as exc:
            table.add_row(inst_name, old_version, "-", f"[bold red]{exc}[/bold red]")

    console.print(table)


# ---------------------------------------------------------------------------
# self-update
# ---------------------------------------------------------------------------


@cli.command("self-update")
def self_update() -> None:
    """Update PocketManager to the latest version."""
    from pocketmanager.core import selfupdate as selfupdate_mod

    console.print("[bold]Checking for updates...[/bold]")
    update = selfupdate_mod.check_for_update()

    if update is None:
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

    if not click.confirm("\nDo you want to update now?", default=True):
        console.print("[dim]Update cancelled.[/dim]")
        return

    console.print()
    success = selfupdate_mod.perform_update()
    if success:
        console.print("\n[bold green]PocketManager updated successfully.[/bold green]")
    else:
        console.print("[bold red]Update failed. Check the logs for details.[/bold red]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--reveal", is_flag=True, default=False, help="Show sensitive values.")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def config(args: tuple[str, ...], reveal: bool) -> None:
    """View or set configuration values.

    With no arguments: print current configuration as JSON.
    With arguments: `config set <key> <value>` to set a value.
    """
    from pocketmanager.core import config as config_mod

    if not args:
        # Print current config
        import copy

        current_config = config_mod.load_config()

        if not reveal:
            current_config = copy.deepcopy(current_config)
            # Mask dashboard_password
            if current_config.get("dashboard_password"):
                current_config["dashboard_password"] = "***"
            # Mask pangolin.api_key
            pangolin = current_config.get("pangolin", {})
            if pangolin.get("api_key"):
                pangolin["api_key"] = "***"

        import json as _json

        pretty = _json.dumps(current_config, indent=2, ensure_ascii=False)
        console.print(pretty, syntax="json")
        return

    if len(args) >= 2 and args[0] == "set":
        key = args[1]
        if len(args) < 3:
            console.print("[bold red]Error: 'config set' requires a key and a value.[/bold red]")
            console.print("[dim]Usage: pm config set <key> <value>[/dim]")
            sys.exit(1)

        value: str | int | float | bool = " ".join(args[2:])

        # Attempt type coercion for common types
        if value.lower() in ("true", "yes"):
            value = True
        elif value.lower() in ("false", "no"):
            value = False
        else:
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass  # keep as string

        config_mod.set(key, value)
        console.print(f"[bold green]Config updated: {key} = {value}[/bold green]")
    else:
        # Treat as a get request — handle optional "get" keyword
        key = args[0] if args[0] != "get" else args[1] if len(args) > 1 else args[0]
        val = config_mod.get(key)
        if val is None:
            console.print(f"[bold red]Error: Config key '{key}' not found.[/bold red]")
            sys.exit(1)
        import json as _json

        console.print(_json.dumps(val, indent=2, ensure_ascii=False))


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
        console.print(f"  [cyan]•[/cyan] {inst.get('name', 'unknown')} (port {inst.get('port', '?')})")


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--port", "dash_port", type=int, default=None, help="Dashboard HTTP port.")
@click.option("--daemon", is_flag=True, default=False, help="Run in background.")
@click.option("--stop", is_flag=True, default=False, help="Stop a running daemon.")
def dashboard(dash_port: int | None, daemon: bool, stop: bool) -> None:
    """Launch the PocketManager web dashboard."""
    from pocketmanager.core.config import get as cfg_get, get_config_dir, load_config, save_config

    pid_path = get_config_dir() / "dashboard.pid"

    # --stop: kill the daemon
    if stop:
        if not pid_path.is_file():
            console.print("[bold red]Error: No dashboard PID file found. Is the dashboard running?[/bold red]")
            sys.exit(1)
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            console.print(f"[bold green]Dashboard stopped (PID {pid}).[/bold green]")
        except ProcessLookupError:
            console.print("[bold yellow]Dashboard process not found — stale PID file removed.[/bold yellow]")
        except Exception as exc:
            console.print(f"[bold red]Error stopping dashboard: {exc}[/bold red]")
            sys.exit(1)
        finally:
            try:
                pid_path.unlink(missing_ok=True)
            except OSError:
                pass
        return

    if dash_port is None:
        dash_port = cfg_get("dashboard_port", 8888)

    # Ensure a dashboard password is set — dashboard must not run unauthenticated
    config = load_config()
    if not config.get("dashboard_password"):
        if daemon:
            console.print("[bold red]Error: Dashboard password is required for network-accessible dashboard.[/bold red]")
            console.print("[dim]Run: pm config set dashboard_password <password>[/dim]")
            sys.exit(1)
        console.print("[bold yellow]No dashboard password set. You must set one before starting the dashboard.[/bold yellow]")
        new_password = click.prompt("Set dashboard password", hide_input=True, confirmation_prompt=True)
        if not new_password:
            console.print("[bold red]Password cannot be empty.[/bold red]")
            sys.exit(1)
        config["dashboard_password"] = new_password
        save_config(config)
        console.print("[bold green]Dashboard password saved.[/bold green]")

    try:
        from pocketmanager.dashboard.app import create_app
    except ImportError as exc:
        console.print(f"[bold red]Error: Dashboard module not available: {exc}[/bold red]")
        console.print("[dim]Install with: pip install flask[/dim]")
        sys.exit(1)

    url = f"http://localhost:{dash_port}"
    console.print(f"[bold]Starting dashboard on {url}...[/bold]")

    if daemon:
        # Fork to background
        pid = os.fork()
        if pid > 0:
            # Parent: write PID file and exit
            pid_path.parent.mkdir(parents=True, exist_ok=True)
            pid_path.write_text(str(pid))
            console.print(f"[bold green]Dashboard running in background (PID: {pid}).[/bold green]")
            console.print(f"[bold]Access it at: {url}[/bold]")
            console.print(f"[dim]Stop with: pm dashboard --stop[/dim]")
            return

        # Child process — run the dashboard
        try:
            app = create_app()
            app.run(host="0.0.0.0", port=dash_port)
        except Exception as exc:
            console.print(f"[bold red]Error starting dashboard: {exc}[/bold red]")
            sys.exit(1)
    else:
        app = create_app()
        console.print(f"[bold green]Dashboard is available at: {url}[/bold green]")
        app.run(host="0.0.0.0", port=dash_port)


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------


@cli.command()
def info() -> None:
    """Show system and PocketManager information."""
    from pocketmanager.core.config import get as cfg_get
    from pocketmanager.core.state import get_all_instances

    instances = get_all_instances()
    base_dir = cfg_get("base_dir", "/home/ubuntu/pocketbases")
    cache_dir = cfg_get("cache_dir", "/home/ubuntu/.pocketmanager/cache")

    # Disk usage of base_dir
    base_disk = "unknown"
    try:
        result = subprocess.run(
            ["du", "-sh", base_dir],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            base_disk = result.stdout.split("\t")[0].strip()
    except Exception:
        pass

    panel_content = (
        f"[bold]OS:[/bold]               {platform.system()} {platform.release()}\n"
        f"[bold]Architecture:[/bold]      {platform.machine()}\n"
        f"[bold]Python:[/bold]            {platform.python_version()}\n"
        f"[bold]PocketManager:[/bold]     {__version__}\n"
        f"[bold]Base directory:[/bold]    {base_dir}\n"
        f"[bold]Cache directory:[/bold]   {cache_dir}\n"
        f"[bold]Instances:[/bold]         {len(instances)}\n"
        f"[bold]Base dir size:[/bold]     {base_disk}"
    )

    console.print(Panel(panel_content, title="System Information", border_style="blue"))


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------


def _require_backup_auth(name: str, instance: dict) -> str | None:
    """Resolve a PocketBase auth token for *instance* or print guidance.

    Returns the token on success, or ``None`` (after printing an error)
    if credentials are missing or authentication fails.
    """
    email = instance.get("superadmin_email")
    password = instance.get("superadmin_password")
    if not email or not password:
        console.print(
            "[bold yellow]No PocketBase superadmin credentials configured "
            f"for '{name}'.[/bold yellow]\n"
            f"[dim]Set them with: pm credentials {name}[/dim]"
        )
        return None

    from pocketmanager.core.backup import authenticate

    port = instance.get("port")
    if not port:
        return None
    url = f"http://localhost:{port}"
    token = authenticate(url, email, password)
    if not token:
        console.print(
            "[bold red]PocketBase authentication failed. "
            "Check the stored superadmin credentials.[/bold red]\n"
            f"[dim]Update with: pm credentials {name}[/dim]"
        )
        return None
    return token


@cli.command()
@click.argument("name")
def credentials(name: str) -> None:
    """Set or update PocketBase superadmin credentials for an instance.

    Credentials are required for backup operations (create, list, download,
    restore).  They are stored in the instance state file (owner-only readable).
    """
    from pocketmanager.core.state import get_instance, update_instance
    from pocketmanager.core.backup import authenticate

    instance = get_instance(name)
    if instance is None:
        console.print(f"[bold red]Error: Instance '{name}' not found.[/bold red]")
        sys.exit(1)

    port = instance.get("port")
    if not port:
        console.print(f"[bold red]Error: No port configured for instance '{name}'.[/bold red]")
        sys.exit(1)

    # Show current state
    current_email = instance.get("superadmin_email")
    if current_email:
        console.print(f"[dim]Current superadmin email: {current_email}[/dim]\n")

    email = click.prompt("Superadmin email")
    password = click.prompt("Superadmin password", hide_input=True)
    if not email or not password:
        console.print("[bold red]Error: Email and password cannot be empty.[/bold red]")
        sys.exit(1)

    # Verify credentials against the instance
    console.print("[dim]Verifying credentials...[/dim]")
    url = f"http://localhost:{port}"
    token = authenticate(url, email, password)
    if not token:
        console.print(
            "[bold red]Authentication failed. Check the email and password.[/bold red]\n"
            "[dim]Make sure the PocketBase superadmin account has been created "
            f"via the admin UI at http://localhost:{port}/_/[/dim]"
        )
        sys.exit(1)

    update_instance(name, {"superadmin_email": email, "superadmin_password": password})
    console.print(f"[bold green]Credentials verified and saved for '{name}'.[/bold green]")
