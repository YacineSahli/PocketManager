"""Credentials and Pangolin auth commands for the PocketManager CLI."""

from __future__ import annotations

import sys

import click
from rich.panel import Panel

from .common import INSTANCE_NAME, _format_auth_status, cli, console

@cli.command()
@click.argument("name", type=INSTANCE_NAME)
def credentials(name: str) -> None:
    """Set or update PocketBase superadmin credentials for an instance.

    Credentials are required for backup operations (create, list, download,
    restore).  They are stored in the instance state file (owner-only readable).
    """
    from pocketmanager.core.state import get_instance, update_instance
    from pocketmanager.core.backup import authenticate, is_instance_reachable

    instance = get_instance(name)
    if instance is None:
        console.print(f"[bold red]Error: Instance '{name}' not found.[/bold red]")
        sys.exit(1)

    port = instance.get("port")
    if not port:
        console.print(f"[bold red]Error: No port configured for instance '{name}'.[/bold red]")
        sys.exit(1)

    # Check the instance is actually up before prompting — credentials can
    # only be verified against a live PocketBase.
    url = f"http://localhost:{port}"
    if not is_instance_reachable(url):
        console.print(
            "[bold red]Instance is not responding — credentials can only be "
            "verified against a running PocketBase.[/bold red]\n"
            f"[dim]Start it with: pm start {name}[/dim]"
        )
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
@click.option("--password", "password_val", is_flag=True, default=False,
              help="Set password authentication (prompts with hidden input).")
@click.option("--no-password", is_flag=True, default=False, help="Remove password authentication.")
def auth(name: str, password_val: bool, no_password: bool) -> None:
    """Manage Pangolin authentication for a PocketBase instance.

    Without options, displays the current authentication status.

    Use --password to set a password (hidden prompt), or --no-password to
    remove it.
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

    # Resolve flag -> concrete password before the status branch
    new_password: str | None = None
    if password_val:
        new_password = click.prompt("Pangolin resource password", hide_input=True, confirmation_prompt=True)

    # No flags: show current auth status
    if not new_password and not no_password:
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

    if new_password is not None:
        if not pangolin_mod.set_resource_password(resource_id, new_password):
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
