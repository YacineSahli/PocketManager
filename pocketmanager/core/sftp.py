"""SFTP off-site backup storage for PocketManager.

Uploads, lists, and prunes backup archives on a remote SFTP server
(e.g. Hetzner Storagebox).  All functions accept an *sftp_config* dict
matching the ``sftp`` section in PocketManager's ``config.json``.

Typical config::

    {
        "sftp": {
            "enabled": true,
            "host": "your-storagebox.your-storagebox.de",
            "port": 23,
            "username": "u123456-sub1",
            "password": "s3cret",
            "private_key_path": "",
            "remote_path": "/backups",
            "max_remote_backups": 30
        }
    }

Remote layout — per-instance folders::

    /backups/
      myapp/
        pb_backup_acme_20260530143000.zip
        pb_backup_acme_20260531030000.zip
      otherapp/
        pb_backup_acme_20260530150000.zip
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Generator

import paramiko

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def _resolve_host(host: str, port: int) -> tuple[str, int]:
    """Resolve *host* to an IP address, preferring reachable ones.

    Oracle Cloud VPSes often lack IPv6 but DNS returns AAAA records.
    Paramiko tries IPv6 first and fails with "Network is unreachable"
    instead of falling back.  This resolves the hostname ourselves and
    returns the first reachable ``(ip, port)`` pair.

    If no address is reachable, returns the IPv4 address anyway so the
    actual error (e.g. connection refused) is more useful than "Network
    is unreachable".
    """
    import socket

    ipv4_addr: str | None = None

    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            results = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
            if results:
                addr = results[0][4]
                ip: str = addr[0]  # type: ignore[assignment]
                # Remember the first IPv4 address as fallback
                if family == socket.AF_INET and ipv4_addr is None:
                    ipv4_addr = ip
                # Quick reachability check
                s = socket.socket(family, socket.SOCK_STREAM)
                s.settimeout(5)
                try:
                    s.connect(addr)
                    s.close()
                    return ip, port
                except OSError:
                    s.close()
                    continue
        except socket.gaierror:
            continue

    # Nothing reachable — prefer IPv4 so the error message is useful
    if ipv4_addr:
        return ipv4_addr, port
    return host, port


def _create_client(sftp_config: dict[str, Any]) -> paramiko.SSHClient:
    """Create and return a connected :class:`paramiko.SSHClient`.

    Uses password auth when *password* is set, otherwise tries the
    *private_key_path* (or falls back to the default ssh-agent /
    ~/.ssh/keys).
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    host: str = sftp_config.get("host", "")
    port: int = sftp_config.get("port", 22)
    username: str = sftp_config.get("username", "")
    password: str = sftp_config.get("password", "")
    key_path: str = sftp_config.get("private_key_path", "")

    # Resolve hostname to a reachable IP (handles IPv6-only DNS on
    # hosts without IPv6 connectivity, e.g. Oracle Cloud VPSes).
    resolved_host, resolved_port = _resolve_host(host, port)

    connect_kwargs: dict[str, Any] = {
        "hostname": resolved_host,
        "port": resolved_port,
        "username": username,
        "timeout": 15,
        "allow_agent": False,
        "look_for_keys": False,
    }

    if password:
        connect_kwargs["password"] = password
    elif key_path:
        connect_kwargs["key_filename"] = os.path.expanduser(key_path)
    else:
        # Fallback: let paramiko try ssh-agent / default keys
        connect_kwargs["allow_agent"] = True
        connect_kwargs["look_for_keys"] = True

    client.connect(**connect_kwargs)
    return client


def _open_sftp(sftp_config: dict[str, Any]) -> paramiko.SFTPClient:
    """Connect and return an open SFTP session."""
    client = _create_client(sftp_config)
    transport = client.get_transport()
    if transport is None:
        raise RuntimeError("SSH transport is None")
    return client.open_sftp()


def test_connection(sftp_config: dict[str, Any]) -> tuple[bool, str]:
    """Verify that the SFTP server is reachable and the remote path exists.

    Returns ``(True, remote_path)`` on success or ``(False, error_message)``
    on failure.
    """
    try:
        sftp = _open_sftp(sftp_config)
    except Exception as exc:
        return False, str(exc)

    try:
        remote_path: str = sftp_config.get("remote_path", "/backups")
        try:
            sftp.stat(remote_path)
        except FileNotFoundError:
            # Create the top-level remote directory
            sftp.mkdir(remote_path)
        return True, remote_path
    except Exception as exc:
        return False, str(exc)
    finally:
        sftp.close()


# ---------------------------------------------------------------------------
# Remote path helpers
# ---------------------------------------------------------------------------


def _instance_remote_dir(sftp_config: dict[str, Any], instance_name: str) -> str:
    """Return the remote directory for *instance_name*.

    Example: ``/backups/myapp``
    """
    base = sftp_config.get("remote_path", "/backups").rstrip("/")
    return f"{base}/{instance_name}"


def _ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    """Recursively create *remote_dir* on the SFTP server (like ``mkdir -p``)."""
    parts = PurePosixPath(remote_dir).parts
    current = ""
    for part in parts:
        if not part:
            continue
        current = f"{current}/{part}" if current else f"/{part}"
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def upload_backup(
    local_path: str,
    instance_name: str,
    sftp_config: dict[str, Any],
    *,
    progress_callback: Any | None = None,
) -> tuple[bool, str]:
    """Upload a local backup file to the remote SFTP server.

    Files are stored under ``<remote_path>/<instance_name>/<filename>``.

    Parameters
    ----------
    local_path:
        Absolute path to the local backup zip file.
    instance_name:
        PocketManager instance name (used as the remote subfolder).
    sftp_config:
        The ``sftp`` config dict.
    progress_callback:
        Optional callable ``(transferred: int, total: int)`` for progress
        reporting.

    Returns ``(True, remote_file_path)`` on success or
    ``(False, error_message)`` on failure.
    """
    if not os.path.isfile(local_path):
        return False, f"Local file not found: {local_path}"

    filename = os.path.basename(local_path)
    remote_dir = _instance_remote_dir(sftp_config, instance_name)
    remote_file = f"{remote_dir}/{filename}"
    file_size = os.path.getsize(local_path)

    try:
        sftp = _open_sftp(sftp_config)
    except Exception as exc:
        return False, f"SFTP connection failed: {exc}"

    try:
        _ensure_remote_dir(sftp, remote_dir)
        sftp.put(local_path, remote_file)
        # Verify upload by checking remote file size
        remote_stat = sftp.stat(remote_file)
        if remote_stat.st_size != file_size:
            # Size mismatch — remove the corrupt remote file
            try:
                sftp.remove(remote_file)
            except Exception:
                pass
            return False, (
                f"Size mismatch after upload: local={file_size}, "
                f"remote={remote_stat.st_size}"
            )
        return True, remote_file
    except Exception as exc:
        return False, str(exc)
    finally:
        sftp.close()


def upload_instance_backup(
    backup_key: str,
    instance_name: str,
    instance_dir: str,
    sftp_config: dict[str, Any],
    *,
    auth_token: str | None = None,
    progress_callback: Any | None = None,
) -> tuple[bool, str]:
    """Download a backup from PocketBase and upload it to SFTP.

    Convenience function that:
    1. Downloads the backup from PocketBase to the instance directory
    2. Uploads the local file to SFTP
    3. Deletes the local downloaded copy

    Returns ``(True, remote_file_path)`` on success or
    ``(False, error_message)`` on failure.
    """
    from pocketmanager.core.backup import download_backup
    from pocketmanager.core.state import get_instance

    # Build local download path
    local_path = f"{instance_dir}/{backup_key}"

    # Download from PocketBase
    instance = get_instance(instance_name)
    if not instance or not instance.get("port"):
        return False, f"Instance '{instance_name}' not found or has no port"

    instance_url = f"http://localhost:{instance['port']}"
    ok = download_backup(instance_url, backup_key, local_path, auth_token=auth_token)
    if not ok:
        return False, f"Failed to download backup '{backup_key}' from PocketBase"

    # Upload to SFTP
    success, result = upload_backup(
        local_path, instance_name, sftp_config,
        progress_callback=progress_callback,
    )

    # Clean up local file
    try:
        os.remove(local_path)
    except OSError:
        pass

    return success, result


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def list_remote_backups(
    instance_name: str,
    sftp_config: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]] | str]:
    """List backup files stored remotely for *instance_name*.

    Returns ``(True, [dict, ...])`` where each dict has ``filename``,
    ``size``, ``last_modified`` keys.  On failure returns
    ``(False, error_message)``.
    """
    remote_dir = _instance_remote_dir(sftp_config, instance_name)

    try:
        sftp = _open_sftp(sftp_config)
    except Exception as exc:
        return False, f"SFTP connection failed: {exc}"

    try:
        entries: list[dict[str, Any]] = []
        try:
            for attr in sftp.listdir_attr(remote_dir):
                # Only include .zip files
                if not attr.filename.endswith(".zip"):
                    continue
                entries.append({
                    "filename": attr.filename,
                    "size": attr.st_size,
                    "last_modified": attr.st_mtime,
                })
        except FileNotFoundError:
            # No remote directory for this instance yet
            pass

        # Sort by filename (which contains timestamp) descending
        entries.sort(key=lambda e: e["filename"], reverse=True)
        return True, entries
    except Exception as exc:
        return False, str(exc)
    finally:
        sftp.close()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def delete_remote_backup(
    backup_filename: str,
    instance_name: str,
    sftp_config: dict[str, Any],
) -> tuple[bool, str]:
    """Delete a single remote backup file.

    Returns ``(True, message)`` on success or ``(False, error_message)``
    on failure.
    """
    remote_dir = _instance_remote_dir(sftp_config, instance_name)
    remote_file = f"{remote_dir}/{backup_filename}"

    try:
        sftp = _open_sftp(sftp_config)
    except Exception as exc:
        return False, f"SFTP connection failed: {exc}"

    try:
        sftp.remove(remote_file)
        return True, f"Deleted remote backup: {remote_file}"
    except FileNotFoundError:
        return False, f"Remote backup not found: {remote_file}"
    except Exception as exc:
        return False, str(exc)
    finally:
        sftp.close()


# ---------------------------------------------------------------------------
# Cleanup / Prune
# ---------------------------------------------------------------------------


def cleanup_remote_backups(
    instance_name: str,
    sftp_config: dict[str, Any],
    max_keep: int | None = None,
) -> tuple[int, list[str]]:
    """Remove old remote backups exceeding *max_keep*.

    Keeps the *max_keep* most recent backups (sorted by filename /
    timestamp) and deletes the rest.

    Parameters
    ----------
    instance_name:
        The instance name.
    sftp_config:
        The ``sftp`` config dict.
    max_keep:
        Maximum number of backups to keep.  Defaults to the value in
        *sftp_config* (``max_remote_backups``).

    Returns ``(deleted_count, [deleted_filename, ...])``.
    """
    if max_keep is None:
        max_keep = sftp_config.get("max_remote_backups", 30)

    ok, result = list_remote_backups(instance_name, sftp_config)
    if not ok:
        return 0, []

    entries: list[dict[str, Any]] = result  # type: ignore[assignment]
    if max_keep is None or len(entries) <= max_keep:
        return 0, []

    to_delete = entries[max_keep:]  # oldest entries (already sorted desc)
    deleted: list[str] = []

    for entry in to_delete:
        filename = entry["filename"]
        success, _ = delete_remote_backup(filename, instance_name, sftp_config)
        if success:
            deleted.append(filename)
            logger.info("Pruned remote backup: %s/%s", instance_name, filename)

    return len(deleted), deleted
