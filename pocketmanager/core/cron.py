"""System cron management for PocketManager.

Manages crontab entries for automated backup jobs.  Uses a **marker
comment** system so PocketManager-owned lines can be identified,
updated, or removed without affecting other cron entries.

PocketManager cron lines look like::

    # <PocketManager-sftp-backup>
    0 3 * * * /usr/local/bin/pm backup-all --push >> /var/log/pm-backup.log 2>&1
    # </PocketManager-sftp-backup>
"""

from __future__ import annotations

import subprocess
from typing import Any


# ---------------------------------------------------------------------------
# Marker constants
# ---------------------------------------------------------------------------

_SFTP_MARKER = "PocketManager-sftp-backup"


# ---------------------------------------------------------------------------
# Crontab helpers
# ---------------------------------------------------------------------------


def _read_crontab(user: str = "root") -> str:
    """Read the current crontab for *user*.  Returns empty string if none."""
    try:
        result = subprocess.run(
            ["sudo", "crontab", "-l", "-u", user],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
        # crontab -l returns 1 when no crontab exists
        return ""
    except Exception:
        return ""


def _write_crontab(content: str, user: str = "root") -> bool:
    """Write *content* as the crontab for *user*.  Returns ``True`` on success."""
    try:
        subprocess.run(
            ["sudo", "crontab", "-u", user, "-"],
            input=content.encode("utf-8"),
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _find_marker_block(lines: list[str], marker: str) -> tuple[int, int] | None:
    """Find the start/end indices of a marker block in *lines*.

    Returns ``(start, end)`` inclusive indices, or ``None`` if not found.
    """
    start_tag = f"# <{marker}>"
    end_tag = f"# </{marker}>"
    start_idx = None
    for i, line in enumerate(lines):
        if line.strip() == start_tag:
            start_idx = i
        elif line.strip() == end_tag and start_idx is not None:
            return start_idx, i
    return None


def _remove_marker_block(lines: list[str], marker: str) -> list[str]:
    """Remove a marker block from *lines* and return the new list."""
    block = _find_marker_block(lines, marker)
    if block is None:
        return lines
    start, end = block
    return lines[:start] + lines[end + 1:]


# ---------------------------------------------------------------------------
# SFTP backup cron
# ---------------------------------------------------------------------------


def get_sftp_cron() -> dict[str, Any]:
    """Return the current SFTP backup cron configuration.

    Returns a dict with keys:

    * ``active`` (bool) — whether the cron entry exists.
    * ``schedule`` (str) — cron expression (e.g. ``"0 3 * * *"``).
    * ``command`` (str) — the full command line.
    * ``raw`` (str) — the raw cron line.
    """
    content = _read_crontab()
    lines = content.splitlines()
    block = _find_marker_block(lines, _SFTP_MARKER)

    result: dict[str, Any] = {
        "active": False,
        "schedule": "",
        "command": "",
        "raw": "",
    }

    if block is None:
        return result

    start, end = block
    # Find the actual cron line(s) between markers
    for i in range(start + 1, end):
        line = lines[i].strip()
        if line and not line.startswith("#"):
            parts = line.split(None, 5)
            if len(parts) >= 6:
                result["active"] = True
                result["schedule"] = " ".join(parts[:5])
                result["command"] = parts[5]
                result["raw"] = line
            break

    return result


def set_sftp_cron(schedule: str, pm_path: str | None = None) -> bool:
    """Create or update the SFTP backup cron entry.

    Parameters
    ----------
    schedule:
        Cron expression (e.g. ``"0 3 * * *"``).
    pm_path:
        Full path to the ``pm`` binary.  Auto-detected if not provided.

    Returns ``True`` on success.
    """
    if pm_path is None:
        pm_path = _find_pm_binary()

    command = f"{pm_path} backup-all --push >> /var/log/pm-backup.log 2>&1"
    cron_line = f"{schedule} {command}"

    content = _read_crontab()
    lines = content.splitlines()

    # Remove existing block
    lines = _remove_marker_block(lines, _SFTP_MARKER)

    # Append new block
    if lines and lines[-1].strip():
        lines.append("")  # blank line before block
    lines.append(f"# <{_SFTP_MARKER}>")
    lines.append(cron_line)
    lines.append(f"# </{_SFTP_MARKER}>")

    new_content = "\n".join(lines) + "\n"
    return _write_crontab(new_content)


def remove_sftp_cron() -> bool:
    """Remove the SFTP backup cron entry.  Returns ``True`` on success."""
    content = _read_crontab()
    lines = content.splitlines()
    lines = _remove_marker_block(lines, _SFTP_MARKER)

    # Clean up trailing blank lines
    while lines and not lines[-1].strip():
        lines.pop()

    new_content = "\n".join(lines)
    if new_content:
        new_content += "\n"
    return _write_crontab(new_content)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_pm_binary() -> str:
    """Locate the ``pm`` binary on the system."""
    import shutil

    path = shutil.which("pm")
    if path:
        return path
    # Common install locations
    for candidate in (
        "/usr/local/bin/pm",
        "/usr/bin/pm",
        "/home/ubuntu/pocketmanager/.venv/bin/pm",
    ):
        import os

        if os.path.isfile(candidate):
            return candidate
    return "pm"
