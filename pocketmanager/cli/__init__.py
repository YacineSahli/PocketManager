"""PocketManager CLI — manage multiple PocketBase instances on a single VPS."""
from pocketmanager.cli.common import PMGroup, cli, console
# Command modules register their commands on the shared `cli` group.
# Import order mirrors the original cli.py registration order.
from pocketmanager.cli import (  # noqa: F401
    backups,
    config_cmd,
    credentials_auth,
    dashboard_cmd,
    instances,
    sftp_cmd,
    updates,
)
__all__ = ["cli", "console", "PMGroup"]
