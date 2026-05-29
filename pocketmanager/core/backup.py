"""PocketBase backup API wrapper.

Provides helpers for creating, listing, downloading, restoring, and
deleting backups on a running PocketBase instance via its HTTP API.

Authentication is not handled in this module — callers are responsible for
ensuring the instance is accessible (e.g. running behind localhost or
behind auth middleware).  Future versions will add superuser-token support.

All functions take *instance_url* as the base URL of the PocketBase instance
(e.g. ``"http://localhost:8101"``) and wrap HTTP calls in ``try / except``.
"""

from __future__ import annotations

from typing import Any

import requests


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_backup(instance_url: str, name: str | None = None) -> bool:
    """Trigger a backup on *instance_url*.

    Sends ``POST {instance_url}/api/backups`` with an optional ``name``.
    Returns ``True`` if the server responded with a success status code.
    """
    try:
        body: dict[str, Any] | None = None
        if name is not None:
            body = {"name": name}
        resp = requests.post(
            f"{instance_url}/api/backups",
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def list_backups(instance_url: str) -> list[dict]:
    """List all backups on *instance_url*.

    Sends ``GET {instance_url}/api/backups`` and returns the list of backup
    objects (each containing ``key``, ``modified``, ``size``).
    Returns an empty list on failure.
    """
    try:
        resp = requests.get(
            f"{instance_url}/api/backups",
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def download_backup(
    instance_url: str,
    backup_key: str,
    dest_path: str,
) -> bool:
    """Download a backup archive from *instance_url* to *dest_path*.

    Sends ``GET {instance_url}/api/backups/{backup_key}?token={token}``.
    Note: downloading typically requires a short-lived file token which is
    not yet handled here.  The call will be extended once auth support is
    added.

    Returns ``True`` on success, ``False`` on failure.
    """
    try:
        resp = requests.get(
            f"{instance_url}/api/backups/{backup_key}",
            timeout=60,
            stream=True,
        )
        resp.raise_for_status()
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=8192):
                fh.write(chunk)
        return True
    except Exception:
        return False


def restore_backup(instance_url: str, backup_key: str) -> bool:
    """Restore a backup on *instance_url*.

    Sends ``POST {instance_url}/api/backups/{backup_key}/restore``.
    Returns ``True`` on success (expected 204).
    """
    try:
        resp = requests.post(
            f"{instance_url}/api/backups/{backup_key}/restore",
            timeout=60,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def delete_backup(instance_url: str, backup_key: str) -> bool:
    """Delete a backup on *instance_url*.

    Sends ``DELETE {instance_url}/api/backups/{backup_key}``.
    Returns ``True`` on success.
    """
    try:
        resp = requests.delete(
            f"{instance_url}/api/backups/{backup_key}",
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def configure_auto_backup(
    instance_url: str,
    cron: str,
    max_keep: int,
) -> bool:
    """Configure automatic backups on *instance_url*.

    Sends ``PATCH {instance_url}/api/settings`` with backup schedule
    settings.

    Parameters
    ----------
    instance_url:
        Base URL of the PocketBase instance.
    cron:
        Cron expression for the backup schedule
        (e.g. ``"0 3 * * *"`` for daily at 3 AM).
    max_keep:
        Maximum number of backup files to retain.

    Returns ``True`` on success.
    """
    try:
        resp = requests.patch(
            f"{instance_url}/api/settings",
            json={
                "backups": {
                    "cron": cron,
                    "cronMaxKeep": max_keep,
                },
            },
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False
