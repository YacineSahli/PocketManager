"""Shared infrastructure for the PocketManager CLI package."""

from __future__ import annotations

import subprocess
from datetime import timedelta
from typing import Any

import click
from rich.console import Console

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


class PMGroup(click.Group):
    """Click group that converts subprocess failures into clean errors.

    sudo/systemctl/cp failures raise ``subprocess.CalledProcessError``, which
    individual commands don't (and shouldn't have to) catch — without this,
    the most common failure mode (sudo password expired) dumps a traceback.
    """

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except subprocess.CalledProcessError as exc:
            cmd = " ".join(str(part) for part in (exc.cmd or []))
            console.print(
                f"[bold red]Error:[/bold red] command failed (exit "
                f"{exc.returncode}): {cmd}\n"
                "[dim]If this was a sudo permission issue, re-run with valid "
                "sudo credentials (e.g. `sudo -v` first).[/dim]"
            )
            ctx.exit(1)


@click.group(cls=PMGroup)
@click.version_option(version=__version__, prog_name="pocketmanager")
def cli() -> None:
    """PocketManager — manage multiple PocketBase instances on a single VPS."""


# ---------------------------------------------------------------------------
# Shared auth / SFTP config helpers
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
            f"[dim]Create one via the admin UI, then run: pm credentials {name}[/dim]"
        )
        return None

    from pocketmanager.core.backup import authenticate, is_instance_reachable

    port = instance.get("port")
    if not port:
        return None
    url = f"http://localhost:{port}"
    token = authenticate(url, email, password)
    if not token:
        if not is_instance_reachable(url):
            console.print(
                "[bold red]PocketBase is not responding. The instance may be "
                f"stopped.[/bold red]\n"
                f"[dim]Start it with: pm start {name}[/dim]"
            )
        else:
            console.print(
                "[bold red]PocketBase authentication failed. "
                "Check the stored superadmin credentials.[/bold red]\n"
                f"[dim]Update with: pm credentials {name}[/dim]"
            )
        return None
    return token


# ---------------------------------------------------------------------------
# Shared backup helpers
# ---------------------------------------------------------------------------


def _latest_backup_key(all_backups: list[dict], backup_name: str | None) -> str | None:
    """Resolve which backup to use from a listing (shared logic).

    If *backup_name* is given, return the key of the first backup whose key
    starts with it; otherwise sort by modified (descending) and return the
    most recent backup's key. Returns ``None`` when nothing resolves or the
    resolved key is empty — callers print their own site-specific warning.
    """
    target = None
    if backup_name:
        for b in all_backups:
            if b.get("key", "").startswith(backup_name):
                target = b
                break
    if not target:
        # Sort by modified descending and take the most recent
        all_backups.sort(key=lambda b: b.get("modified", ""), reverse=True)
        target = all_backups[0] if all_backups else None

    backup_key = target.get("key", "") if target else ""
    return backup_key or None


def _upload_and_prune(
    backup_key: str,
    name: str,
    instance_dir: str,
    sftp_config: dict[str, Any],
    token: str,
) -> tuple[bool, str, int, list]:
    """Upload a backup to SFTP and prune old remote backups (shared logic).

    Returns ``(ok, result, deleted_count, deleted_files)``. Pruning only
    happens when the upload succeeded; printing and exit semantics stay at
    the call sites.
    """
    from pocketmanager.core.sftp import cleanup_remote_backups, upload_instance_backup

    ok, result = upload_instance_backup(
        backup_key, name, instance_dir, sftp_config, auth_token=token,
    )
    if not ok:
        return False, result, 0, []

    # Prune old remote backups
    max_keep = sftp_config.get("max_remote_backups", 30)
    deleted_count, deleted_files = cleanup_remote_backups(
        name, sftp_config, max_keep=max_keep,
    )
    return True, result, deleted_count, deleted_files
