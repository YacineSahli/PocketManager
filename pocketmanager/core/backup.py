"""PocketBase backup API wrapper.

Provides helpers for creating, listing, downloading, restoring, and
deleting backups on a running PocketBase instance via its HTTP API.

All PocketBase backup endpoints require **superuser authentication**.
The functions in this module accept an ``auth_token`` parameter that
should be a valid PocketBase superuser JWT obtained via
:func:`get_instance_auth_token`.

Use ``pm credentials <name>`` to configure superuser credentials for
an instance, then all backup operations will authenticate automatically.
"""

from __future__ import annotations

from typing import Any

import requests


# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------

# PocketBase changed the superuser auth endpoint between versions.
# We try the newer endpoint first and fall back to the legacy one.
_AUTH_ENDPOINTS = (
    "/api/collections/superusers/auth-with-password",  # v0.23+
    "/api/admins/auth-with-password",                   # v0.22 and earlier
)


def authenticate(
    instance_url: str,
    email: str,
    password: str,
) -> str | None:
    """Authenticate as a PocketBase superuser and return a JWT token.

    Tries both the modern (v0.23+) and legacy (pre-v0.23) auth endpoints.

    Returns the token string on success, or ``None`` on failure.
    """
    for endpoint in _AUTH_ENDPOINTS:
        try:
            resp = requests.post(
                f"{instance_url}{endpoint}",
                json={"identity": email, "password": password},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                token: str | None = data.get("token")
                if token:
                    return token
            if resp.status_code == 404:
                # Endpoint doesn't exist — try the next one.
                continue
        except Exception:
            continue
    return None


def get_instance_auth_token(instance_name: str) -> str | None:
    """Look up stored credentials for *instance_name* and obtain an auth token.

    Returns ``None`` if the instance is not found, has no stored credentials,
    or authentication fails.
    """
    from pocketmanager.core.state import get_instance

    inst = get_instance(instance_name)
    if not inst or not inst.get("port"):
        return None

    email = inst.get("superadmin_email")
    password = inst.get("superadmin_password")
    if not email or not password:
        return None

    url = f"http://localhost:{inst['port']}"
    return authenticate(url, email, password)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_backup(
    instance_url: str,
    name: str | None = None,
    *,
    auth_token: str | None = None,
) -> bool:
    """Trigger a backup on *instance_url*.

    Sends ``POST {instance_url}/api/backups`` with an optional ``name``.
    Returns ``True`` if the server responded with a success status code.
    """
    try:
        headers: dict[str, str] = {}
        if auth_token:
            headers["Authorization"] = auth_token
        body: dict[str, Any] | None = None
        if name is not None:
            body = {"name": name}
        resp = requests.post(
            f"{instance_url}/api/backups",
            json=body,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def list_backups(
    instance_url: str,
    *,
    auth_token: str | None = None,
) -> list[dict]:
    """List all backups on *instance_url*.

    Sends ``GET {instance_url}/api/backups`` and returns the list of backup
    objects (each containing ``key``, ``modified``, ``size``).
    Returns an empty list on failure.
    """
    try:
        headers: dict[str, str] = {}
        if auth_token:
            headers["Authorization"] = auth_token
        resp = requests.get(
            f"{instance_url}/api/backups",
            headers=headers,
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
    *,
    auth_token: str | None = None,
) -> bool:
    """Download a backup archive from *instance_url* to *dest_path*.

    Sends ``GET {instance_url}/api/backups/{backup_key}``.
    Returns ``True`` on success, ``False`` on failure.
    """
    try:
        headers: dict[str, str] = {}
        if auth_token:
            headers["Authorization"] = auth_token
        resp = requests.get(
            f"{instance_url}/api/backups/{backup_key}",
            headers=headers,
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


def restore_backup(
    instance_url: str,
    backup_key: str,
    *,
    auth_token: str | None = None,
) -> bool:
    """Restore a backup on *instance_url*.

    Sends ``POST {instance_url}/api/backups/{backup_key}/restore``.
    PocketBase automatically restarts the process after a successful restore.
    Returns ``True`` on success (expected 204).
    """
    try:
        headers: dict[str, str] = {}
        if auth_token:
            headers["Authorization"] = auth_token
        resp = requests.post(
            f"{instance_url}/api/backups/{backup_key}/restore",
            headers=headers,
            timeout=60,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def delete_backup(
    instance_url: str,
    backup_key: str,
    *,
    auth_token: str | None = None,
) -> bool:
    """Delete a backup on *instance_url*.

    Sends ``DELETE {instance_url}/api/backups/{backup_key}``.
    Returns ``True`` on success.
    """
    try:
        headers: dict[str, str] = {}
        if auth_token:
            headers["Authorization"] = auth_token
        resp = requests.delete(
            f"{instance_url}/api/backups/{backup_key}",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def get_backup_count(instance_name: str) -> int:
    """Return the number of backups for *instance_name*.

    Looks up the instance URL from state, authenticates with stored
    credentials, and calls :func:`list_backups`.
    Returns ``0`` on any failure (including missing credentials).
    """
    try:
        from pocketmanager.core.state import get_instance

        inst = get_instance(instance_name)
        if not inst or not inst.get("port"):
            return 0

        url = f"http://localhost:{inst['port']}"
        token = get_instance_auth_token(instance_name)
        return len(list_backups(url, auth_token=token))
    except Exception:
        return 0


def configure_auto_backup(
    instance_url: str,
    cron: str,
    max_keep: int,
    *,
    auth_token: str | None = None,
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
    auth_token:
        PocketBase superuser JWT.

    Returns ``True`` on success.
    """
    try:
        headers: dict[str, str] = {}
        if auth_token:
            headers["Authorization"] = auth_token
        resp = requests.patch(
            f"{instance_url}/api/settings",
            json={
                "backups": {
                    "cron": cron,
                    "cronMaxKeep": max_keep,
                },
            },
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False
