"""Systemd timer management for PocketManager.

Manages a systemd timer+service pair for automated SFTP backup jobs,
replacing the previous root-crontab approach.

Two unit files are written to ``/etc/systemd/system/``:

* ``pm-sftp-backup.service`` — oneshot service that runs
  ``pm backup-all --push``
* ``pm-sftp-backup.timer`` — timer that activates the service on schedule

The service runs as the user who owns the ``pm`` binary (typically the
installing user), **not** as root.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SERVICE_NAME = "pm-sftp-backup"
_SERVICE_PATH = Path(f"/etc/systemd/system/{_SERVICE_NAME}.service")
_TIMER_PATH = Path(f"/etc/systemd/system/{_SERVICE_NAME}.timer")


# ---------------------------------------------------------------------------
# Cron → OnCalendar conversion
# ---------------------------------------------------------------------------


def _cron_to_oncalendar(cron_expr: str) -> str:
    """Convert a 5-field cron expression to a systemd ``OnCalendar`` value.

    Supported patterns::

        M H    * * *     → *-*-* HH:MM:00
        M H    */N * *   → *-*-* HH:MM:00  (interval not fully supported)
        M H    * * DOW   → DOW *-*-* HH:MM:00

    Falls back to ``*-*-* HH:MM:00`` if parsing fails.
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return "*-*-* 03:00:00"

    minute_s, hour_s, dom_s, month_s, dow_s = parts

    # Day-of-week mapping (cron 0-7 → systemd Mon..Sun)
    dow_map = {
        "0": "Sun", "7": "Sun",
        "1": "Mon", "2": "Tue", "3": "Wed",
        "4": "Thu", "5": "Fri", "6": "Sat",
    }

    # Build time part
    try:
        hour = int(hour_s) if hour_s.isdigit() else 0
        minute = int(minute_s) if minute_s.isdigit() else 0
        time_part = f"{hour:02d}:{minute:02d}:00"
    except (ValueError, TypeError):
        time_part = "03:00:00"

    # Day-of-week
    if dow_s != "*" and not dow_s.startswith("*/"):
        # Could be "0", "1,3,5", etc.
        days = []
        for d in dow_s.split(","):
            d = d.strip()
            days.append(dow_map.get(d, d))
        dow_part = ",".join(days) + " *-*-*"
    else:
        dow_part = "*-*-*"

    return f"{dow_part} {time_part}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_pm_binary() -> str:
    """Locate the ``pm`` binary on the system."""
    # Prefer the binary that is currently running
    current = shutil.which("pm")
    if current:
        return current

    # Common install locations (ordered by likelihood)
    home = os.path.expanduser("~")
    for candidate in (
        f"{home}/.local/bin/pm",
        "/usr/local/bin/pm",
        "/usr/bin/pm",
    ):
        if os.path.isfile(candidate):
            return candidate

    return "pm"


def _get_service_user() -> str:
    """Determine the unprivileged user that should run the backup service.

    Uses the owner of the ``pm`` binary, falling back to the current user.
    """
    pm_path = _find_pm_binary()
    try:
        stat_info = os.stat(pm_path)
        import pwd
        return pwd.getpwuid(stat_info.st_uid).pw_name
    except (OSError, KeyError):
        return os.environ.get("USER", "ubuntu")


def _systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a systemctl command via sudo."""
    return subprocess.run(
        ["sudo", "systemctl", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _write_unit_file(path: Path, content: str) -> None:
    """Write a systemd unit file (via sudo tee)."""
    subprocess.run(
        ["sudo", "tee", str(path)],
        input=content,
        capture_output=True,
        text=True,
        check=True,
    )


def _remove_unit_file(path: Path) -> None:
    """Remove a systemd unit file (via sudo rm)."""
    subprocess.run(
        ["sudo", "rm", "-f", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Service & timer unit templates
# ---------------------------------------------------------------------------


def _service_unit_content(pm_path: str, user: str) -> str:
    """Return the content of the oneshot service unit."""
    return f"""[Unit]
Description=PocketManager SFTP Backup
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User={user}
ExecStart={pm_path} backup-all --push
StandardOutput=journal
StandardError=journal
"""


def _timer_unit_content(oncalendar: str) -> str:
    """Return the content of the timer unit."""
    return f"""[Unit]
Description=PocketManager SFTP Backup Timer

[Timer]
OnCalendar={oncalendar}
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
"""


# ---------------------------------------------------------------------------
# Legacy crontab cleanup
# ---------------------------------------------------------------------------


def _cleanup_legacy_crontab() -> None:
    """Remove any legacy PocketManager entries from root's crontab."""
    try:
        result = subprocess.run(
            ["sudo", "crontab", "-l", "-u", "root"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return

        marker_start = "# <PocketManager-sftp-backup>"
        marker_end = "# </PocketManager-sftp-backup>"

        lines = result.stdout.splitlines()
        new_lines: list[str] = []
        skip = False
        for line in lines:
            if line.strip() == marker_start:
                skip = True
                continue
            if line.strip() == marker_end:
                skip = False
                continue
            if not skip:
                new_lines.append(line)

        if len(new_lines) != len(lines):
            # Something was removed — write back
            content = "\n".join(new_lines).strip() + "\n" if new_lines else ""
            subprocess.run(
                ["sudo", "crontab", "-u", "root", "-"],
                input=content,
                capture_output=True, text=True, check=True,
            )
    except Exception:
        pass  # Best-effort cleanup


# ---------------------------------------------------------------------------
# Public API (same interface as before)
# ---------------------------------------------------------------------------


def get_sftp_cron() -> dict[str, Any]:
    """Return the current SFTP backup timer status.

    Returns a dict with keys:

    * ``active`` (bool) — whether the timer is loaded and active.
    * ``schedule`` (str) — the OnCalendar schedule (human-readable).
    * ``command`` (str) — the command executed by the service.
    * ``raw`` (str) — the raw ``OnCalendar=`` line from the timer unit.
    """
    result: dict[str, Any] = {
        "active": False,
        "schedule": "",
        "command": "",
        "raw": "",
    }

    # Check if timer unit exists and is active
    try:
        status = _systemctl("is-active", f"{_SERVICE_NAME}.timer", check=False)
        if status.stdout.strip() == "active":
            result["active"] = True
    except Exception:
        pass

    # Read the OnCalendar value from the timer unit
    if _TIMER_PATH.is_file():
        try:
            content = subprocess.run(
                ["sudo", "cat", str(_TIMER_PATH)],
                capture_output=True, text=True, check=True,
            ).stdout
            for line in content.splitlines():
                if line.strip().startswith("OnCalendar="):
                    oncal = line.strip().split("=", 1)[1]
                    result["raw"] = oncal
                    result["schedule"] = _oncalendar_to_human(oncal)
                    break
        except Exception:
            pass

    # Read the command from the service unit
    if _SERVICE_PATH.is_file():
        try:
            content = subprocess.run(
                ["sudo", "cat", str(_SERVICE_PATH)],
                capture_output=True, text=True, check=True,
            ).stdout
            for line in content.splitlines():
                if line.strip().startswith("ExecStart="):
                    result["command"] = line.strip().split("=", 1)[1]
                    break
        except Exception:
            pass

    return result


def set_sftp_cron(schedule: str, pm_path: str | None = None) -> bool:
    """Create or update the SFTP backup systemd timer.

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

    user = _get_service_user()
    oncalendar = _cron_to_oncalendar(schedule)

    try:
        # Write service unit
        _write_unit_file(
            _SERVICE_PATH,
            _service_unit_content(pm_path, user),
        )

        # Write timer unit
        _write_unit_file(
            _TIMER_PATH,
            _timer_unit_content(oncalendar),
        )

        # Reload systemd, enable and start the timer
        _systemctl("daemon-reload")
        _systemctl("enable", f"{_SERVICE_NAME}.timer")
        _systemctl("restart", f"{_SERVICE_NAME}.timer")

        # Clean up any legacy root crontab entry
        _cleanup_legacy_crontab()

        return True
    except Exception:
        return False


def remove_sftp_cron() -> bool:
    """Remove the SFTP backup systemd timer.  Returns ``True`` on success."""
    try:
        _systemctl("stop", f"{_SERVICE_NAME}.timer", check=False)
        _systemctl("disable", f"{_SERVICE_NAME}.timer", check=False)
        _remove_unit_file(_TIMER_PATH)
        _remove_unit_file(_SERVICE_PATH)
        _systemctl("daemon-reload")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _oncalendar_to_human(oncalendar: str) -> str:
    """Convert an OnCalendar value to a human-readable schedule string.

    E.g. ``*-*-* 03:00:00`` → ``daily at 03:00``
    """
    # DOW *-*-* HH:MM:SS → weekly on DOW at HH:MM
    dow_match = re.match(
        r'([A-Z][a-z]{2}(?:,[A-Z][a-z]{2})*)\s+\*-\*-\*\s+(\d{2}:\d{2}):\d{2}',
        oncalendar,
    )
    if dow_match:
        days = dow_match.group(1)
        time = dow_match.group(2)
        return f"weekly on {days} at {time}"

    # *-*-* HH:MM:SS → daily at HH:MM
    daily_match = re.match(r'\*-\*-\*\s+(\d{2}:\d{2}):\d{2}', oncalendar)
    if daily_match:
        return f"daily at {daily_match.group(1)}"

    return oncalendar
