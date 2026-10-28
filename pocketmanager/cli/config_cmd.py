"""Config command for the PocketManager CLI."""

from __future__ import annotations

import sys

import click
from rich.syntax import Syntax

from .common import cli, console

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
            # Mask secrets
            if current_config.get("dashboard_password"):
                current_config["dashboard_password"] = "***"
            if current_config.get("dashboard_pangolin_password"):
                current_config["dashboard_pangolin_password"] = "***"
            pangolin = current_config.get("pangolin", {})
            if pangolin.get("api_key"):
                pangolin["api_key"] = "***"
            sftp_cfg = current_config.get("sftp", {})
            if sftp_cfg.get("password"):
                sftp_cfg["password"] = "***"

        import json as _json

        pretty = _json.dumps(current_config, indent=2, ensure_ascii=False)
        console.print(Syntax(pretty, "json", theme="monokai"))
        return

    if len(args) >= 2 and args[0] == "set":
        key = args[1]
        if len(args) < 3:
            console.print("[bold red]Error: 'config set' requires a key and a value.[/bold red]")
            console.print("[dim]Usage: pm config set <key> <value>[/dim]")
            sys.exit(1)

        raw_value: str = " ".join(args[2:])

        # Type-aware coercion: cast to the type of the CURRENT value so a
        # numeric-looking SFTP password or host stays a string. Unknown keys
        # are stored as strings.
        current = config_mod.get(key)
        value: str | int | float | bool = raw_value
        if isinstance(current, bool):
            if raw_value.lower() in ("true", "yes", "1"):
                value = True
            elif raw_value.lower() in ("false", "no", "0"):
                value = False
        elif isinstance(current, int) and not isinstance(current, bool):
            try:
                value = int(raw_value)
            except ValueError:
                pass
        elif isinstance(current, float):
            try:
                value = float(raw_value)
            except ValueError:
                pass

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
