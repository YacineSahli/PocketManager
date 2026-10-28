"""SFTP configuration and scheduling commands for the PocketManager CLI."""

from __future__ import annotations

import sys

import click
from rich.panel import Panel

from .common import cli, console

# ---------------------------------------------------------------------------
# sftp-config
# ---------------------------------------------------------------------------


@cli.command("sftp-config")
@click.option("--host", "sftp_host", default=None, help="SFTP server hostname.")
@click.option("--port", "sftp_port", default=None, type=int, help="SFTP port (default: 22).")
@click.option("--username", "sftp_user", default=None, help="SFTP username.")
@click.option("--password", "sftp_pass", default=None,
              help="SFTP password (visible in shell history — prefer --prompt-password).")
@click.option("--prompt-password", "sftp_prompt_pass", is_flag=True, default=False,
              help="Prompt for the SFTP password with hidden input.")
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
    sftp_prompt_pass: bool,
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
      pm sftp-config --prompt-password
      pm sftp-config --enable
      pm sftp-config --test
    """
    from pocketmanager.core.config import get, load_config, save_config

    if sftp_prompt_pass:
        sftp_pass = click.prompt("SFTP password", hide_input=True)

    config = load_config()
    sftp = config.setdefault("sftp", {})

    # If no options given at all, show current config and prompt
    has_any_option = any([
        sftp_host, sftp_port is not None, sftp_user, sftp_pass,
        no_password, sftp_key, sftp_remote_path, sftp_max is not None,
        enable is not None, test_only, sftp_prompt_pass,
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

    # Optionally test — only when running interactively; a piped/scripted
    # invocation must not block on a prompt.
    if effective.get("host") and sys.stdin.isatty():
        if click.confirm("Test connection now?", default=True):
            from pocketmanager.core.sftp import test_connection

            ok, msg = test_connection(effective)
            if ok:
                console.print(f"[bold green]Connection successful! Remote path: {msg}[/bold green]")
            else:
                console.print(f"[bold yellow]Connection failed: {msg}[/bold yellow]")
                console.print("[dim]Configuration was saved. Fix connection issues and re-run with --test.[/dim]")


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
    """Configure automatic SFTP off-site backup via a systemd timer.

    When enabled, creates a systemd timer unit that runs
    ``pm backup-all --push`` on the given schedule.

    The timer runs as the user who installed PM (not root).

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
            status_parts.append(f"[bold green]Timer:[/bold green]     enabled")
            status_parts.append(f"[bold]Schedule:[/bold]  {cron_info['schedule']}")
            status_parts.append(f"[dim]Command:[/dim]   {cron_info['command']}")
        else:
            status_parts.append("[dim]Timer:[/dim]     disabled")

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
            console.print("[bold green]SFTP backup timer removed.[/bold green]")
        else:
            console.print("[bold red]Error: Failed to remove timer. Try with sudo.[/bold red]")
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

    try:
        ok = set_sftp_cron(schedule)
    except ValueError as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        sys.exit(1)
    except RuntimeError as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]\n[dim]Try with sudo.[/dim]")
        sys.exit(1)
    if ok:
        console.print(
            f"[bold green]SFTP backup timer installed.[/bold green]\n"
            f"  Schedule: {schedule}\n"
            f"  Command:  pm backup-all --push\n"
            f"  Logs:     journalctl -u pm-sftp-backup.service"
        )
    else:
        console.print("[bold red]Error: Failed to install timer. Try with sudo.[/bold red]")
        sys.exit(1)
