"""Dashboard command for the PocketManager CLI."""

from __future__ import annotations

import os
import signal
import sys

import click
from rich.panel import Panel

from .common import cli, console

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

    # Enable password authentication on the resource. Use a distinct random
    # password — reusing the dashboard's Basic-auth password would mean one
    # leak compromises both layers.
    import secrets as _secrets

    pangolin_password = _secrets.token_urlsafe(16)
    if not set_resource_password(resource_id, pangolin_password):
        console.print(
            "[bold yellow]Resource created but failed to set password authentication.[/bold yellow]"
        )
        console.print("[dim]Set it manually in the Pangolin dashboard.[/dim]")
    else:
        config["dashboard_pangolin_password"] = pangolin_password

    # Save the resource ID so we don't recreate on next start
    config["dashboard_pangolin_resource_id"] = str(resource_id)
    save_config(config)

    domain_name = pangolin_cfg.get("default_domain", "")
    full_domain = f"{subdomain}.{domain_name}" if domain_name else subdomain
    console.print(
        f"[bold green]Pangolin resource '{resource_name}' created at https://{full_domain}[/bold green]"
    )
    if config.get("dashboard_pangolin_password"):
        console.print(
            Panel(
                f"[bold]Pangolin password:[/bold] {config['dashboard_pangolin_password']}\n\n"
                "[dim]This password gates public access to the dashboard via Pangolin "
                "(separate from the dashboard login). It is stored in config.json — "
                "reveal with: pm config --reveal[/dim]",
                title="[bold yellow]Pangolin Resource Password[/bold yellow]",
                border_style="yellow",
            )
        )


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
    dash_host = str(cfg_get("dashboard_host", "127.0.0.1"))

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

    display_host = "localhost" if dash_host in ("127.0.0.1", "::1") else dash_host
    url = f"http://{display_host}:{dash_port}"
    if dash_host == "0.0.0.0":
        console.print(
            "[bold yellow]Warning: dashboard is bound to 0.0.0.0 (dashboard_host in "
            "config) — it is reachable from any network interface and only "
            "protected by its password. Prefer keeping it on loopback and "
            "exposing it through Pangolin or an SSH tunnel.[/bold yellow]"
        )
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

        # Child process — detach from the terminal session so it survives
        # the shell exiting, then run the dashboard.
        try:
            os.setsid()
            app = create_app()
            app.run(host=dash_host, port=dash_port)
        except Exception as exc:
            console.print(f"[bold red]Error starting dashboard: {exc}[/bold red]")
            sys.exit(1)
    else:
        app = create_app()
        console.print(f"[bold green]Dashboard is available at: {url}[/bold green]")
        app.run(host=dash_host, port=dash_port)
