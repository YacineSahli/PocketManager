"""Instance lifecycle commands for the PocketManager CLI."""

from __future__ import annotations

import os
import platform
import subprocess
import sys

import click
from rich.panel import Panel
from rich.table import Table

from pocketmanager import __version__

from .common import (
    INSTANCE_NAME,
    _build_url,
    _format_auth_status,
    _format_uptime,
    _status_style,
    cli,
    console,
)

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

    # Validate the name up front — after the interactive prompts is too late
    # to discover a typo or duplicate cheaply.
    try:
        instance_mod._validate_name(name)
    except ValueError as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        sys.exit(1)

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

        while True:
            port = click.prompt("HTTP port", type=int, default=suggested_port)
            if 1 <= port <= 65535:
                break
            console.print("[bold yellow]Port must be between 1 and 65535.[/bold yellow]")
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
        panel_content += f"\n[bold]Dashboard URL:[/bold] {url}/_/"

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

    # Show warning if the service failed to start
    start_warning = result.get("start_warning") if result else None
    if start_warning:
        console.print()
        console.print(
            Panel(
                f"[bold yellow]Service did not start.[/bold yellow]\n\n{start_warning}",
                title="[bold yellow]⚠ Service[/bold yellow]",
                border_style="yellow",
            )
        )

    # Show warning if the automatic backup schedule could not be enabled
    backup_warning = result.get("backup_warning") if result else None
    if backup_warning:
        console.print()
        console.print(
            Panel(
                f"[bold yellow]Automatic backups not enabled.[/bold yellow]\n\n{backup_warning}",
                title="[bold yellow]⚠ Backups[/bold yellow]",
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
    table.add_column("Dashboard URL", style="green", min_width=20, ratio=2)

    def _local_backup_label(inst: dict) -> str:
        """Local-backup column: query PB's real cron when reachable."""
        fallback = (
            "[bold green]on[/bold green]"
            if inst.get("auto_backup", False)
            else "[dim]off[/dim]"
        )
        if not inst.get("active", False):
            return fallback
        try:
            from pocketmanager.core.backup import (
                get_backup_cron_status,
                get_instance_auth_token,
            )

            token = get_instance_auth_token(inst.get("name", ""))
            cron_status = get_backup_cron_status(
                f"http://127.0.0.1:{inst.get('port', 8090)}",
                auth_token=token,
            )
            if cron_status["enabled"]:
                return "[bold green]on[/bold green]"
        except Exception:
            pass
        return fallback

    # Probe instances concurrently — one hung instance must not stall `pm ls`
    # (each probe can take up to ~15s sequentially).
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, len(instances))) as pool:
        local_labels = list(pool.map(_local_backup_label, instances))

    for inst, local_label in zip(instances, local_labels):
        active = inst.get("active", False)
        url = _build_url(inst)
        dashboard_url = f"{url}/_/" if url != "(unknown)" else "—"

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
        instance_mod.stop_instance(name)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        sys.exit(1)

    console.print(f"[bold green]Instance '{name}' stopped successfully.[/bold green]")


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
    "-d",
    "--domain",
    default=None,
    help="Set the full public domain (e.g. myapp.apps.example.com) and create "
    "the Pangolin resource for this instance.",
)
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
    domain: str | None,
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
      pm modify myapp -d pb.example.com         Set domain + Pangolin resource

    When no modification flags are given, prints the current service file.
    """
    from pocketmanager.core.state import get_instance, update_instance
    from pocketmanager.core.systemd import (
        get_service_path,
        modify_service,
        restart_service,
    )

    # No modification flags: just show the current service file
    if port is None and domain is None and not env and not rm_env:
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

    # Apply service changes
    service_changed = port is not None or bool(env_dict) or bool(rm_env_list)
    if service_changed:
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

    if service_changed:
        console.print(f"[bold green]Service modified:[/bold green] {', '.join(changes)}")

    # Domain / Pangolin attach
    if domain is not None:
        _modify_domain(name, instance, domain)

    if service_changed and do_restart:
        console.print("[dim]Restarting service to apply changes...[/dim]")
        if restart_service(name):
            console.print(f"[bold green]Instance '{name}' restarted.[/bold green]")
        else:
            console.print(
                f"[bold yellow]Warning: Failed to restart '{name}'. "
                f"Apply changes manually with: pm restart {name}[/bold yellow]"
            )
    elif service_changed:
        console.print(
            "[dim]Changes written. Restart to apply: pm restart {name}[/dim]".format(
                name=name
            )
        )


def _modify_domain(name: str, instance: dict, domain: str) -> None:
    """Set the public domain for *name*, creating the Pangolin resource."""
    from pocketmanager.core.config import get as cfg_get
    from pocketmanager.core.state import update_instance

    domain = domain.strip()
    if not domain:
        console.print("[bold red]Error: Domain cannot be empty.[/bold red]")
        sys.exit(1)

    existing_resource = instance.get("pangolin_resource_id")
    if existing_resource:
        console.print(
            "[bold yellow]Note: instance already has a Pangolin resource — "
            "the new domain was recorded in state, but the resource itself "
            "must be updated in the Pangolin dashboard.[/bold yellow]"
        )
        update_instance(name, {"domain": domain})
        console.print(f"[bold green]Domain updated: https://{domain}[/bold green]")
        return

    required = {
        "pangolin.api_url": cfg_get("pangolin.api_url", ""),
        "pangolin.api_key": cfg_get("pangolin.api_key", ""),
        "pangolin.org_id": cfg_get("pangolin.org_id", ""),
        "pangolin.default_domain_id": cfg_get("pangolin.default_domain_id", ""),
       }
    missing = [key for key, val in required.items() if not val]
    if missing:
        console.print(
            "[bold red]Error: Pangolin is not fully configured — cannot create "
            "a resource for the domain.[/bold red]\n"
            f"[dim]Missing: {', '.join(missing)}\n"
            f"Configure Pangolin, then re-run: pm modify {name} -d {domain}[/dim]"
        )
        sys.exit(1)

    # Derive the subdomain the same way `pm create` does.
    default_domain = cfg_get("pangolin.default_domain", "")
    if default_domain and domain.endswith(f".{default_domain}"):
        subdomain = domain[: -(len(default_domain) + 1)]
    else:
        subdomain = domain

    site_id_raw = cfg_get("pangolin.site_id", "")
    site_id = int(site_id_raw) if site_id_raw else 0

    from pocketmanager.core.pangolin import (
        PangolinAPIError,
        PangolinConfigError,
        create_resource,
    )

    console.print(f"[bold]Creating Pangolin resource for https://{domain}...[/bold]")
    try:
        result = create_resource(
            name=name,
            subdomain=subdomain,
            domain_id=required["pangolin.default_domain_id"],
            org_id=required["pangolin.org_id"],
            site_id=site_id,
            target_ip=cfg_get("pangolin.target_ip", "127.0.0.1"),
            target_port=instance.get("port"),
        )
    except (PangolinConfigError, PangolinAPIError) as exc:
        console.print(f"[bold red]Pangolin resource creation failed: {exc}[/bold red]")
        console.print(
            f"[dim]The domain was NOT saved. Fix Pangolin and re-run: "
            f"pm modify {name} -d {domain}[/dim]"
        )
        sys.exit(1)

    resource_id = result.get("resourceId")
    update_instance(name, {"domain": domain, "pangolin_resource_id": resource_id})
    console.print(f"[bold green]Domain set and Pangolin resource created: https://{domain}[/bold green]")
    console.print(
        "[dim]Note: Pangolin enables SSO authentication on new resources — "
        f"open it publicly with: pm auth {name} --no-password[/dim]"
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
        removed = instance_mod.remove_instance(name, keep_data=keep_data)
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

    # Surface cleanup steps that failed — a surviving unit file or a live
    # Pangolin resource is exactly the kind of leftover users need to know about.
    for warning in removed.get("_warnings", []):
        console.print(f"[bold yellow]Warning: {warning}[/bold yellow]")


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
        from pocketmanager.core.systemd import get_journal_logs_ex

        output, stderr, returncode = get_journal_logs_ex(name, lines=lines, follow=False)
        if output:
            console.print(output.rstrip(), highlight=False)
        elif stderr.strip() or returncode != 0:
            # journalctl failed (e.g. permissions) — show why instead of a
            # misleading "No logs found".
            console.print(
                f"[bold red]Error reading logs for '{name}':[/bold red] "
                f"{stderr.strip() or f'journalctl exited with {returncode}'}"
            )
            console.print(
                "[dim]If this is a journal permission error, add yourself to "
                "the systemd-journal group: sudo usermod -aG systemd-journal $USER "
                "(then log out and back in).[/dim]"
            )
            sys.exit(1)
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
        # Non-zero exit so scripts and monitors can trust `pm healthcheck`.
        sys.exit(1)


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

    # Post-update migration pending? Nudge the user towards `pm doctor`.
    from pocketmanager.core.state import get_meta

    if instances and not get_meta("doctor_bind_host_migrate"):
        console.print(
            "[bold yellow]Post-update migration pending — run 'pm doctor' to "
            "rebind instances to the configured bind_host and remove the "
            "legacy root-crontab SFTP job.[/bold yellow]"
        )


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--yes", "assume_yes", is_flag=True, default=False,
              help="Skip the restart confirmation prompt (non-interactive use).")
def doctor(assume_yes: bool) -> None:
    """Apply post-update migrations to existing instances.

    Regenerates every instance's systemd unit with the *configured*
    ``bind_host`` (important after the 2026-10-28 loopback security fix for
    installs whose units still bind 0.0.0.0) and removes the legacy
    root-crontab SFTP backup entry.

    Instances whose units actually change are restarted (with confirmation).
    """
    import re as _re
    from datetime import datetime, timezone

    from pocketmanager.core.config import get as cfg_get
    from pocketmanager.core.cron import cleanup_legacy_crontab
    from pocketmanager.core.state import get_all_instances, set_meta
    from pocketmanager.core.systemd import (
        get_service_path,
        modify_service,
        restart_service,
    )

    instances = get_all_instances()
    configured_bind = str(cfg_get("bind_host", "127.0.0.1"))

    console.print(f"[bold]Configured bind_host:[/bold] {configured_bind}\n")

    to_rebind: list[str] = []
    unchanged: list[str] = []
    missing: list[str] = []

    for inst in instances:
        name = inst.get("name", "")
        service_path = get_service_path(name)
        if not service_path.is_file():
            missing.append(name)
            continue
        content = service_path.read_text(encoding="utf-8")
        current_bind: str | None = None
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("ExecStart=") and "--http=" in stripped:
                match = _re.search(r"--http=([0-9.]+):(\d+)", stripped)
                if match:
                    current_bind = match.group(1)
                break
        if current_bind is None:
            console.print(
                f"[bold yellow]Could not parse the bind address for '{name}' — "
                "regenerating its unit anyway.[/bold yellow]"
            )
            to_rebind.append(name)
        elif current_bind == configured_bind:
            unchanged.append(name)
        else:
            console.print(
                f"[cyan]•[/cyan] {name}: {current_bind} → {configured_bind}"
            )
            to_rebind.append(name)

    if missing:
        for name in missing:
            console.print(f"[bold red]• {name}: no service file found (skipped)[/bold red]")

    if unchanged:
        console.print(
            f"[dim]{len(unchanged)} instance(s) already bound to "
            f"{configured_bind}.[/dim]"
        )

    if to_rebind and not assume_yes:
        console.print()
        if not click.confirm(
            f"Regenerate the unit file(s) for {len(to_rebind)} instance(s) and "
            "restart them? (restarts cause brief downtime)",
            default=True,
        ):
            console.print("[dim]Migration cancelled — re-run 'pm doctor' later.[/dim]")
            return

    failures = 0
    for name in to_rebind:
        try:
            modify_service(name=name, bind_host=configured_bind)
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            failures += 1
            console.print(f"[bold red]Failed to regenerate unit for '{name}': {exc}[/bold red]")
            continue
        if restart_service(name):
            console.print(f"[bold green]• {name}: unit regenerated, restarted[/bold green]")
        else:
            console.print(
                f"[bold yellow]• {name}: unit regenerated but restart failed — "
                f"start manually with: pm start {name}[/bold yellow]"
            )

    # Remove the legacy root-crontab SFTP job from pre-timer installs.
    cleanup_legacy_crontab()
    console.print("[dim]Legacy root-crontab cleanup checked.[/dim]")

    # Record that this migration pass has run so `pm info` stops nudging.
    set_meta(
        "doctor_bind_host_migrate",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    if failures:
        console.print(
            f"[bold red]Doctor finished with {failures} failure(s) — re-run "
            "'pm doctor' after fixing the errors above.[/bold red]"
        )
        sys.exit(1)

    console.print("[bold green]Doctor complete — all migrations applied.[/bold green]")
