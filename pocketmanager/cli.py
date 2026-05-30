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
from typing import Any

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
    """Build the public URL for an instance from its domain config."""
    domain = instance.get("domain")
    port = instance.get("port")

    if domain:
        return f"https://{domain}"
    if port:
        return f"http://localhost:{port}"
    return "(unknown)"


class InstanceNameParam(click.ParamType):
    """Click argument type with shell autocompletion for existing instance names."""

    name = "instance_name"

    def shell_complete(self, ctx: click.Context, param: click.Parameter, incomplete: str) -> list[click.shell_completion.CompletionItem]:
        try:
            from pocketmanager.core.state import get_all_instances

            return [
                click.shell_completion.CompletionItem(i["name"])
                for i in get_all_instances()
                if i["name"].startswith(incomplete)
            ]
        except Exception:
            return []

    def convert(self, value: str, param: click.Parameter | None, ctx: click.Context | None) -> str:
        return value


INSTANCE_NAME = InstanceNameParam()


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


def _format_auth_status(auth_info: dict | None) -> str:
    """Format Pangolin auth info dict into a rich-markup status string."""
    if auth_info is None:
        return "[dim]unknown[/dim]"

    auth_methods: list[str] = []
    if auth_info.get("blockAccess"):
        auth_methods.append("blocked")
    if auth_info.get("sso"):
        auth_methods.append("SSO")
    if auth_info.get("password"):
        auth_methods.append("password")
    if auth_info.get("pincode"):
        auth_methods.append("pincode")
    if auth_info.get("whitelist"):
        auth_methods.append("whitelist")
    if auth_info.get("headerAuth"):
        auth_methods.append("header")

    if auth_methods:
        return f"[bold yellow]enabled[/bold yellow] ({', '.join(auth_methods)})"
    return "[bold green]disabled[/bold green] (public)"


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
@click.option("-d", "--domain", default=None, help="Full domain (e.g. myapp.apps.example.com).")
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
    env: tuple[str, ...],
    pb_version: str | None,
    no_pangolin: bool,
) -> None:
    """Create a new PocketBase instance."""
    from pocketmanager.core import instance as instance_mod

    # Interactive mode: no options provided
    interactive = port is None and domain is None and not env and pb_version is None

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
        f"[bold]Version:[/bold]  {result.get('version', pb_version or 'latest')}"
    )

    # Show admin UI link only when no auto-created credentials
    admin_email = result.get("superadmin_email")
    admin_password = result.get("superadmin_password")
    if not (admin_email and admin_password):
        panel_content += f"\n[bold]Admin UI:[/bold]  {url}/_/"

    console.print(Panel(panel_content, title="[bold green]Instance Created[/bold green]", border_style="green"))

    # Show auto-generated superadmin credentials
    if admin_email and admin_password:
        console.print()
        console.print(
            Panel(
                f"[bold]Email:[/bold]    {admin_email}\n"
                f"[bold]Password:[/bold] {admin_password}\n\n"
                f"[dim]These are stored for backup operations. "
                f"Change with: pm credentials {name}[/dim]",
                title="[bold yellow]Superadmin Credentials[/bold yellow]",
                border_style="yellow",
            )
        )

    # Show warning if auto-creation failed
    admin_warning = result.get("admin_warning")
    if admin_warning:
        console.print()
        console.print(
            Panel(
                f"[bold yellow]Superadmin not auto-created.[/bold yellow]\n\n{admin_warning}",
                title="[bold yellow]⚠ Superadmin[/bold yellow]",
                border_style="yellow",
            )
        )

    # Show Pangolin warning if resource creation was skipped or failed
    pangolin_warning = result.get("pangolin_warning") if result else None
    if pangolin_warning:
        console.print()
        console.print(
            Panel(
                f"[bold yellow]Pangolin resource not created.[/bold yellow]\n\n{pangolin_warning}",
                title="[bold yellow]⚠ Pangolin[/bold yellow]",
                border_style="yellow",
            )
        )


# ---------------------------------------------------------------------------
# list / ls
# ---------------------------------------------------------------------------


@cli.command("ls")
def list_instances_cmd() -> None:
    """List all PocketBase instances."""
    from pocketmanager.core import instance as instance_mod
    from pocketmanager.core.cron import get_sftp_cron

    instances = instance_mod.list_instances()

    if not instances:
        console.print("[dim]No instances found.[/dim]")
        return

    # Check SFTP backup cron status (shared across all instances)
    sftp_cron = get_sftp_cron()
    sftp_active = sftp_cron["active"]

    table = Table(title="PocketBase Instances")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Port", justify="right", style="magenta")
    table.add_column("Status", justify="center")
    table.add_column("Version", style="dim")
    table.add_column("Local Backup", justify="center")
    table.add_column("SFTP Backup", justify="center")
    table.add_column("Dashboard URL", style="green")

    for inst in instances:
        active = inst.get("active", False)
        url = _build_url(inst)
        dashboard_url = f"{url}/_/" if url != "(unknown)" else "—"

        # Local backup status
        auto_backup = inst.get("auto_backup", False)
        if auto_backup:
            local_label = "[bold green]on[/bold green]"
        else:
            local_label = "[dim]off[/dim]"

        # SFTP backup status (global)
        if sftp_active:
            sftp_label = "[bold green]on[/bold green]"
        else:
            sftp_label = "[dim]off[/dim]"

        table.add_row(
            inst.get("name", ""),
            str(inst.get("port", "")),
            _status_style(active),
            inst.get("version", ""),
            local_label,
            sftp_label,
            dashboard_url,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name", type=INSTANCE_NAME)
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
@click.argument("name", type=INSTANCE_NAME)
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
@click.argument("name", type=INSTANCE_NAME)
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
# modify
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name", type=INSTANCE_NAME)
@click.option("-p", "--port", type=int, default=None, help="New HTTP port for the instance.")
@click.option(
    "-e",
    "--env",
    multiple=True,
    help="Set an environment variable (KEY=VAL). Repeatable.",
)
@click.option(
    "--rm-env",
    multiple=True,
    help="Remove an environment variable by key. Repeatable.",
)
@click.option(
    "--restart/--no-restart",
    "do_restart",
    default=True,
    help="Restart the service after modification (default: --restart).",
)
def modify(
    name: str,
    port: int | None,
    env: tuple[str, ...],
    rm_env: tuple[str, ...],
    do_restart: bool,
) -> None:
    """Modify the systemd service configuration for an instance.

    \b
    Examples:
      pm modify myapp -e TZ=UTC -e FOO=bar     Add/set env vars
      pm modify myapp --rm-env FOO              Remove an env var
      pm modify myapp -p 8091                   Change the port
      pm modify myapp -p 8091 -e FOO=bar        Change port + add env
      pm modify myapp --no-restart -e A=b       Modify without restarting

    When no modification flags are given, prints the current service file.
    """
    from pocketmanager.core.state import get_instance, update_instance
    from pocketmanager.core.systemd import (
        get_service_path,
        modify_service,
        restart_service,
    )

    # No modification flags: just show the current service file
    if port is None and not env and not rm_env:
        service_path = get_service_path(name)
        if not service_path.is_file():
            console.print(
                f"[bold red]Error: No service file found for '{name}'.[/bold red]"
            )
            sys.exit(1)
        console.print(service_path.read_text(encoding="utf-8"), highlight=False)
        return

    # Validate instance exists
    instance = get_instance(name)
    if instance is None:
        console.print(f"[bold red]Error: Instance '{name}' not found.[/bold red]")
        sys.exit(1)

    # Parse env vars
    env_dict: dict[str, str] = {}
    for item in env:
        if "=" not in item:
            console.print(
                f"[bold red]Error:[/bold red] Invalid env format '{item}'. Use KEY=VAL."
            )
            sys.exit(1)
        key, _, value = item.partition("=")
        env_dict[key] = value

    rm_env_list = list(rm_env)

    # Apply changes
    console.print(f"[bold]Modifying service for '{name}'...[/bold]")
    try:
        modify_service(
            name=name,
            port=port,
            env_set=env_dict or None,
            env_unset=rm_env_list or None,
        )
    except FileNotFoundError as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        sys.exit(1)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        sys.exit(1)

    # Update persisted instance state so port/env stay in sync
    state_updates: dict = {}
    if port is not None:
        state_updates["port"] = port
    if env_dict or rm_env_list:
        current_env = dict(instance.get("env", {}))
        for key in rm_env_list:
            current_env.pop(key, None)
        current_env.update(env_dict)
        state_updates["env"] = current_env
    if state_updates:
        update_instance(name, state_updates)

    # Build a summary of what changed
    changes: list[str] = []
    if port is not None:
        changes.append(f"port → {port}")
    for k, v in env_dict.items():
        changes.append(f"env {k}={v}")
    for k in rm_env_list:
        changes.append(f"env –{k}")

    console.print(f"[bold green]Service modified:[/bold green] {', '.join(changes)}")

    if do_restart:
        console.print("[dim]Restarting service to apply changes...[/dim]")
        if restart_service(name):
            console.print(f"[bold green]Instance '{name}' restarted.[/bold green]")
        else:
            console.print(
                f"[bold yellow]Warning: Failed to restart '{name}'. "
                "Apply changes manually with: pm restart {name}[/bold yellow]"
            )
    else:
        console.print(
            "[dim]Changes written. Restart to apply: pm restart {name}[/dim]".format(
                name=name
            )
        )


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name", type=INSTANCE_NAME)
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
@click.argument("name", type=INSTANCE_NAME)
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

    # Domain-based dashboard URL
    url = _build_url(info)
    if url != "(unknown)":
        panel_content += f"\n[bold]Dashboard URL:[/bold] {url}/_/"

    # Pangolin auth status
    resource_id = info.get("pangolin_resource_id")
    if resource_id:
        panel_content += f"\n[bold]Auth:[/bold]         {_format_auth_status(info.get('pangolin_auth'))}"

    if env_vars:
        env_lines = "\n".join(f"  [dim]{k}[/dim]={v}" for k, v in env_vars.items())
        panel_content += f"\n[bold]Environment:[/bold]\n{env_lines}"

    console.print(Panel(panel_content, title=f"Instance: {name}", border_style="cyan"))


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name", type=INSTANCE_NAME)
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
# SFTP helpers
# ---------------------------------------------------------------------------


def _require_sftp_config() -> dict[str, Any] | None:
    """Load SFTP config and verify it is enabled.

    Returns the config dict on success, or ``None`` (after printing an
    error message).
    """
    from pocketmanager.core.config import get

    sftp_config: dict[str, Any] = {
        "enabled": get("sftp.enabled", False),
        "host": get("sftp.host", ""),
        "port": get("sftp.port", 22),
        "username": get("sftp.username", ""),
        "password": get("sftp.password", ""),
        "private_key_path": get("sftp.private_key_path", ""),
        "remote_path": get("sftp.remote_path", "backups"),
        "max_remote_backups": get("sftp.max_remote_backups", 30),
    }

    if not sftp_config.get("enabled"):
        console.print(
            "[bold red]Error: SFTP off-site backup is not configured.[/bold red]\n"
            "[dim]Run 'pm sftp-config' to set up remote storage.[/dim]"
        )
        return None

    if not sftp_config.get("host"):
        console.print(
            "[bold red]Error: SFTP host is not configured.[/bold red]\n"
            "[dim]Run 'pm sftp-config' to set the host.[/dim]"
        )
        return None

    return sftp_config


def _push_latest_backup(
    name: str,
    instance: dict[str, Any],
    instance_url: str,
    token: str,
    backup_name: str | None,
) -> None:
    """Upload the most recent backup to SFTP."""
    from pocketmanager.core import backup as backup_mod
    from pocketmanager.core.sftp import (
        cleanup_remote_backups,
        upload_instance_backup,
    )

    sftp_config = _require_sftp_config()
    if sftp_config is None:
        sys.exit(1)

    # Find the backup we just created
    all_backups = backup_mod.list_backups(instance_url, auth_token=token)
    if not all_backups:
        console.print("[bold yellow]Warning: Could not list backups for SFTP upload.[/bold yellow]")
        return

    target = None
    if backup_name:
        for b in all_backups:
            if b.get("key", "").startswith(backup_name):
                target = b
                break
    if not target:
        all_backups.sort(key=lambda b: b.get("modified", ""), reverse=True)
        target = all_backups[0]

    backup_key = target.get("key", "")
    if not backup_key:
        console.print("[bold yellow]Warning: Could not identify backup key for SFTP upload.[/bold yellow]")
        return

    instance_dir = instance.get("instance_dir", "")
    console.print(f"[bold]Uploading backup to SFTP server...[/bold]")

    ok, result = upload_instance_backup(
        backup_key, name, instance_dir, sftp_config, auth_token=token,
    )
    if ok:
        console.print(f"[bold green]Backup uploaded to: {result}[/bold green]")
        # Prune old remote backups
        max_keep = sftp_config.get("max_remote_backups", 30)
        deleted_count, deleted_files = cleanup_remote_backups(
            name, sftp_config, max_keep=max_keep,
        )
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
# sftp-config
# ---------------------------------------------------------------------------


@cli.command("sftp-config")
@click.option("--host", "sftp_host", default=None, help="SFTP server hostname.")
@click.option("--port", "sftp_port", default=None, type=int, help="SFTP port (default: 22).")
@click.option("--username", "sftp_user", default=None, help="SFTP username.")
@click.option("--password", "sftp_pass", default=None, help="SFTP password (use --no-password for key-only auth).")
@click.option("--no-password", is_flag=True, default=False,
              help="Clear stored password (use key-based auth).")
@click.option("--private-key", "sftp_key", default=None,
              help="Path to SSH private key file.")
@click.option("--remote-path", "sftp_remote_path", default=None,
              help="Remote directory for backups (default: backups).")
@click.option("--max-remote-backups", "sftp_max", default=None, type=int,
              help="Maximum remote backups to keep per instance (default: 30).")
@click.option("--enable/--disable", default=None,
              help="Enable or disable SFTP off-site backups.")
@click.option("--test", "test_only", is_flag=True, default=False,
              help="Test the connection without saving.")
def sftp_config(
    sftp_host: str | None,
    sftp_port: int | None,
    sftp_user: str | None,
    sftp_pass: str | None,
    no_password: bool,
    sftp_key: str | None,
    sftp_remote_path: str | None,
    sftp_max: int | None,
    enable: bool | None,
    test_only: bool,
) -> None:
    """Configure or test SFTP off-site backup storage.

    Configure connection details for a remote SFTP server (e.g. Hetzner
    Storagebox).  All settings are optional — only provided options are
    updated.

    \b
    Examples:
      pm sftp-config --host u123456.your-storagebox.de --port 23 --username u123456-sub1
      pm sftp-config --password
      pm sftp-config --enable
      pm sftp-config --test
    """
    from pocketmanager.core.config import get, load_config, save_config

    config = load_config()
    sftp = config.setdefault("sftp", {})

    # If no options given at all, show current config and prompt
    has_any_option = any([
        sftp_host, sftp_port is not None, sftp_user, sftp_pass,
        no_password, sftp_key, sftp_remote_path, sftp_max is not None,
        enable is not None, test_only,
    ])

    if not has_any_option:
        # Interactive mode — show current and prompt
        current_host = sftp.get("host", "")
        current_port = sftp.get("port", 22)
        current_user = sftp.get("username", "")
        current_path = sftp.get("remote_path", "backups")
        current_max = sftp.get("max_remote_backups", 30)
        current_enabled = sftp.get("enabled", False)

        console.print(Panel(
            f"[bold]Host:[/bold]     {current_host or '(not set)'}\n"
            f"[bold]Port:[/bold]     {current_port}\n"
            f"[bold]Username:[/bold] {current_user or '(not set)'}\n"
            f"[bold]Path:[/bold]     {current_path}\n"
            f"[bold]Max keep:[/bold] {current_max}\n"
            f"[bold]Enabled:[/bold]  {'yes' if current_enabled else 'no'}",
            title="Current SFTP Configuration",
            border_style="cyan",
        ))
        return

    # Apply individual settings
    if sftp_host is not None:
        sftp["host"] = sftp_host
    if sftp_port is not None:
        sftp["port"] = sftp_port
    if sftp_user is not None:
        sftp["username"] = sftp_user
    if sftp_pass is not None:
        sftp["password"] = sftp_pass
    if no_password:
        sftp["password"] = ""
    if sftp_key is not None:
        sftp["private_key_path"] = sftp_key
    if sftp_remote_path is not None:
        sftp["remote_path"] = sftp_remote_path
    if sftp_max is not None:
        sftp["max_remote_backups"] = sftp_max
    if enable is not None:
        sftp["enabled"] = enable

    # Build the effective config for testing / display
    effective = {
        "enabled": sftp.get("enabled", False),
        "host": sftp.get("host", ""),
        "port": sftp.get("port", 22),
        "username": sftp.get("username", ""),
        "password": sftp.get("password", ""),
        "private_key_path": sftp.get("private_key_path", ""),
        "remote_path": sftp.get("remote_path", "backups"),
        "max_remote_backups": sftp.get("max_remote_backups", 30),
    }

    if test_only:
        if not effective.get("host"):
            console.print("[bold red]Error: No SFTP host configured.[/bold red]")
            sys.exit(1)

        console.print(f"[bold]Testing connection to {effective['host']}:{effective['port']}...[/bold]")
        from pocketmanager.core.sftp import test_connection

        ok, msg = test_connection(effective)
        if ok:
            console.print(f"[bold green]Connection successful! Remote path: {msg}[/bold green]")
        else:
            console.print(f"[bold red]Connection failed: {msg}[/bold red]")
            sys.exit(1)
        return

    # Save
    config["sftp"] = sftp
    save_config(config)
    console.print("[bold green]SFTP configuration saved.[/bold green]")

    # Optionally test
    if effective.get("host"):
        if click.confirm("Test connection now?", default=True):
            from pocketmanager.core.sftp import test_connection

            ok, msg = test_connection(effective)
            if ok:
                console.print(f"[bold green]Connection successful! Remote path: {msg}[/bold green]")
            else:
                console.print(f"[bold yellow]Connection failed: {msg}[/bold yellow]")
                console.print("[dim]Configuration was saved. Fix connection issues and re-run with --test.[/dim]")


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
    from pocketmanager.core.sftp import cleanup_remote_backups, upload_instance_backup
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
        all_backups.sort(key=lambda b: b.get("modified", ""), reverse=True)
        backup_key = all_backups[0].get("key", "")

    if not backup_key:
        console.print("[bold red]Error: Could not determine backup key.[/bold red]")
        sys.exit(1)

    console.print(f"[bold]Uploading '{backup_key}' for '{name}' to SFTP...[/bold]")
    ok, result = upload_instance_backup(
        backup_key, name, instance_dir, sftp_config, auth_token=token,
    )
    if ok:
        console.print(f"[bold green]Backup uploaded to: {result}[/bold green]")
        # Prune old remote backups
        max_keep = sftp_config.get("max_remote_backups", 30)
        deleted_count, deleted_files = cleanup_remote_backups(
            name, sftp_config, max_keep=max_keep,
        )
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
        # Show current status
        auto_backup = instance.get("auto_backup", False)
        schedule = instance.get("backup_cron", "")
        keep = instance.get("backup_max_keep", "")

        status = "[bold green]enabled[/bold green]" if auto_backup else "[dim]disabled[/dim]"
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
# sftp-backup-schedule
# ---------------------------------------------------------------------------


@cli.command("sftp-backup-schedule")
@click.option("--enable/--disable", default=None,
              help="Enable or disable the SFTP backup cron job.")
@click.option("--schedule", "cron_expr", default=None,
              help="Cron expression (e.g. '0 3 * * *').")
def sftp_backup_schedule(
    enable: bool | None,
    cron_expr: str | None,
) -> None:
    """Configure automatic SFTP off-site backup via system cron.

    When enabled, creates a system cron entry that runs
    ``pm backup-all --push`` on the given schedule.

    \b
    Examples:
      pm sftp-backup-schedule --enable --schedule '0 3 * * *'
      pm sftp-backup-schedule --disable
      pm sftp-backup-schedule
    """
    from pocketmanager.core.cron import get_sftp_cron, remove_sftp_cron, set_sftp_cron
    from pocketmanager.core.config import get as cfg_get

    has_any_option = any([enable is not None, cron_expr is not None])

    if not has_any_option:
        # Show current status
        cron_info = get_sftp_cron()
        sftp_enabled = cfg_get("sftp.enabled", False)
        sftp_host = cfg_get("sftp.host", "")

        status_parts = []
        if sftp_enabled and sftp_host:
            status_parts.append(f"[bold green]SFTP:[/bold green]     {sftp_host}")
        else:
            status_parts.append("[dim]SFTP:[/dim]      not configured")

        if cron_info["active"]:
            status_parts.append(f"[bold green]Cron:[/bold green]      enabled")
            status_parts.append(f"[bold]Schedule:[/bold]  {cron_info['schedule']}")
            status_parts.append(f"[dim]Command:[/dim]   {cron_info['command']}")
        else:
            status_parts.append("[dim]Cron:[/dim]      disabled")

        console.print(Panel(
            "\n".join(status_parts),
            title="SFTP Backup Schedule",
            border_style="cyan",
        ))
        return

    if enable is False or (enable is None and not cron_expr):
        # Disable
        ok = remove_sftp_cron()
        if ok:
            console.print("[bold green]SFTP backup cron removed.[/bold green]")
        else:
            console.print("[bold red]Error: Failed to remove cron entry. Try with sudo.[/bold red]")
            sys.exit(1)
        return

    # Enable / update
    schedule = cron_expr or "0 3 * * *"

    # Verify SFTP is configured
    if not cfg_get("sftp.enabled", False):
        console.print(
            "[bold red]Error: SFTP is not configured.[/bold red]\n"
            "[dim]Run 'pm sftp-config --enable' first.[/dim]"
        )
        sys.exit(1)

    if not cfg_get("sftp.host", ""):
        console.print(
            "[bold red]Error: SFTP host is not configured.[/bold red]\n"
            "[dim]Run 'pm sftp-config --host <hostname>' first.[/dim]"
        )
        sys.exit(1)

    ok = set_sftp_cron(schedule)
    if ok:
        console.print(
            f"[bold green]SFTP backup cron installed.[/bold green]\n"
            f"  Schedule: {schedule}\n"
            f"  Command:  pm backup-all --push\n"
            f"  Log:      /var/log/pm-backup.log"
        )
    else:
        console.print("[bold red]Error: Failed to install cron entry. Try with sudo.[/bold red]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# backup-all
# ---------------------------------------------------------------------------


@cli.command("backup-all")
@click.option("--push", "push_remote", is_flag=True, default=False,
              help="Upload each backup to SFTP after creation.")
def backup_all(push_remote: bool) -> None:
    """Create a backup of all PocketBase instances.

    Iterates every registered instance, creates a backup, and optionally
    pushes it to the configured SFTP server.  Designed for use in cron:

    \b
      0 3 * * * /usr/local/bin/pm backup-all --push >> /var/log/pm-backup.log 2>&1
    """
    from pocketmanager.core import backup as backup_mod
    from pocketmanager.core.sftp import cleanup_remote_backups, upload_instance_backup
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

    for inst in instances:
        inst_name = inst.get("name", "")
        port = inst.get("port")
        if not port:
            row = [inst_name, "-", "[bold red]no port[/bold red]"]
            if push_remote:
                row.append("-")
            table.add_row(*row)
            continue

        instance_url = f"http://localhost:{port}"
        token = _require_backup_auth(inst_name, inst)

        if token is None:
            row = [inst_name, "-", "[bold red]no credentials[/bold red]"]
            if push_remote:
                row.append("-")
            table.add_row(*row)
            continue

        # Create backup
        ok = backup_mod.create_backup(instance_url, auth_token=token)
        if not ok:
            row = [inst_name, "-", "[bold red]failed[/bold red]"]
            if push_remote:
                row.append("-")
            table.add_row(*row)
            continue

        # Find the backup we just created
        all_backups = backup_mod.list_backups(instance_url, auth_token=token)
        all_backups.sort(key=lambda b: b.get("modified", ""), reverse=True)
        backup_key = all_backups[0].get("key", "unknown") if all_backups else "unknown"

        row = [inst_name, backup_key, "[bold green]ok[/bold green]"]

        if push_remote and sftp_config:
            instance_dir = inst.get("instance_dir", "")
            ok_sftp, result = upload_instance_backup(
                backup_key, inst_name, instance_dir, sftp_config, auth_token=token,
            )
            if ok_sftp:
                row.append("[bold green]uploaded[/bold green]")
                # Prune old remote backups
                max_keep = sftp_config.get("max_remote_backups", 30)
                deleted_count, _ = cleanup_remote_backups(
                    inst_name, sftp_config, max_keep=max_keep,
                )
                if deleted_count:
                    row[-1] = f"[bold green]uploaded[/bold green] [dim]({deleted_count} pruned)[/dim]"
            else:
                row.append(f"[bold red]{result}[/bold red]")

        table.add_row(*row)

    console.print(table)


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


def _setup_dashboard_pangolin(config: dict, dash_port: int, daemon: bool) -> None:
    """Ensure a Pangolin public resource exists for the dashboard (interactive).

    If the resource already exists (tracked via config), skip silently.
    Otherwise, prompt the user for a name and create the resource with
    password authentication enabled.
    """
    from pocketmanager.core.config import save_config
    from pocketmanager.core.pangolin import (
        PangolinAPIError,
        PangolinConfigError,
        create_resource,
        list_resources,
        set_resource_password,
    )

    # Already set up — nothing to do
    if config.get("dashboard_pangolin_resource_id"):
        return

    pangolin_cfg = config.get("pangolin", {})
    api_key = pangolin_cfg.get("api_key", "")
    org_id = pangolin_cfg.get("org_id", "")
    api_url = pangolin_cfg.get("api_url", "")

    # Pangolin not configured — skip silently
    if not (api_key and org_id and api_url):
        return

    # In daemon mode we can't prompt — skip
    if daemon:
        return

    # Check if a resource named "pocketmanager" (or similar) already exists
    default_name = "pocketmanager"
    try:
        existing = list_resources(org_id)
        existing_names = {r.get("name", "") for r in existing}
        # Find a name that doesn't conflict
        if default_name in existing_names:
            n = 2
            while f"{default_name}-{n}" in existing_names:
                n += 1
            default_name = f"{default_name}-{n}"
    except Exception:
        pass

    console.print()
    console.print(
        "[bold cyan]Pangolin is configured but the dashboard has no public resource yet.[/bold cyan]"
    )
    console.print(
        "A public resource will expose the dashboard through Pangolin with password authentication."
    )

    resource_name = click.prompt(
        "Resource name for the dashboard",
        default=default_name,
    )
    if not resource_name.strip():
        console.print("[dim]Skipping Pangolin resource creation.[/dim]")
        return

    resource_name = resource_name.strip()

    # Use the resource name directly as the subdomain for Pangolin
    subdomain = resource_name

    domain_id = pangolin_cfg.get("default_domain_id", "")
    site_id_raw = pangolin_cfg.get("site_id", "")
    site_id = int(site_id_raw) if site_id_raw else 0
    target_ip = pangolin_cfg.get("target_ip", "127.0.0.1")

    try:
        result = create_resource(
            name=resource_name,
            subdomain=subdomain,
            domain_id=domain_id,
            org_id=org_id,
            site_id=site_id,
            target_ip=target_ip,
            target_port=dash_port,
        )
    except (PangolinConfigError, PangolinAPIError) as exc:
        console.print(f"[bold red]Failed to create Pangolin resource: {exc}[/bold red]")
        console.print("[dim]You can set it up manually later.[/dim]")
        return

    resource_id = result.get("resourceId")
    if not resource_id:
        console.print("[bold yellow]Resource created but no ID returned — skipping auth setup.[/bold yellow]")
        return

    # Enable password authentication on the resource
    dashboard_password = config.get("dashboard_password", "")
    if dashboard_password:
        if not set_resource_password(resource_id, dashboard_password):
            console.print(
                "[bold yellow]Resource created but failed to set password authentication.[/bold yellow]"
            )
            console.print("[dim]Set it manually in the Pangolin dashboard.[/dim]")

    # Save the resource ID so we don't recreate on next start
    config["dashboard_pangolin_resource_id"] = str(resource_id)
    save_config(config)

    domain_name = pangolin_cfg.get("default_domain", "")
    full_domain = f"{subdomain}.{domain_name}" if domain_name else subdomain
    console.print(
        f"[bold green]Pangolin resource '{resource_name}' created at https://{full_domain}[/bold green]"
    )
    console.print("[dim]Password authentication is enabled via Pangolin.[/dim]")


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

    # Optionally create a Pangolin public resource for the dashboard
    _setup_dashboard_pangolin(config, dash_port, daemon)  # type: ignore[arg-type]

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
@click.argument("name", type=INSTANCE_NAME)
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


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name", type=INSTANCE_NAME)
@click.option("--password", "password_val", default=None, help="Set password authentication.")
@click.option("--no-password", is_flag=True, default=False, help="Remove password authentication.")
def auth(name: str, password_val: str | None, no_password: bool) -> None:
    """Manage Pangolin authentication for a PocketBase instance.

    Without options, displays the current authentication status.

    Use --password <value> to set a password, or --no-password to remove it.
    """
    from pocketmanager.core.state import get_instance

    instance = get_instance(name)
    if instance is None:
        console.print(f"[bold red]Error: Instance '{name}' not found.[/bold red]")
        sys.exit(1)

    resource_id = instance.get("pangolin_resource_id")
    if not resource_id:
        console.print(
            f"[bold yellow]Instance '{name}' has no Pangolin resource.[/bold yellow]\n"
            "[dim]Authentication management requires a Pangolin resource. "
            "Re-create the instance without --no-pangolin to enable this feature.[/dim]"
        )
        sys.exit(1)

    from pocketmanager.core import pangolin as pangolin_mod

    # No flags: show current auth status
    if not password_val and not no_password:
        auth_info = pangolin_mod.get_resource_auth_info(resource_id)
        panel_lines = f"[bold]Resource ID:[/bold]  {resource_id}\n"
        panel_lines += f"[bold]Auth:[/bold]         {_format_auth_status(auth_info)}"

        if auth_info:
            panel_lines += (
                f"\n[bold]  SSO:[/bold]       {'on' if auth_info.get('sso') else '[dim]off[/dim]'}"
                f"\n[bold]  Password:[/bold]  {'on' if auth_info.get('password') else '[dim]off[/dim]'}"
            )
            if auth_info.get("pincode"):
                panel_lines += "\n[bold]  Pincode:[/bold]   on"
            if auth_info.get("whitelist"):
                panel_lines += "\n[bold]  Whitelist:[/bold]  on"
            if auth_info.get("headerAuth"):
                panel_lines += "\n[bold]  Header:[/bold]     on"

        console.print(Panel(panel_lines, title=f"Authentication: {name}", border_style="cyan"))
        return

    # Apply changes
    success = True

    if password_val is not None:
        if not pangolin_mod.set_resource_password(resource_id, password_val):
            console.print("[bold red]Error: Failed to set password authentication.[/bold red]")
            success = False
        else:
            console.print("[bold green]Password authentication set.[/bold green]")

    if no_password:
        if not pangolin_mod.remove_resource_password(resource_id):
            console.print("[bold red]Error: Failed to remove password authentication.[/bold red]")
            success = False
        else:
            console.print("[bold green]Password authentication removed.[/bold green]")

    if not success:
        sys.exit(1)
