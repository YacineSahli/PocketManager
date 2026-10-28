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

_DOW_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
_DOW_INDEX = {name.lower(): i for i, name in enumerate(_DOW_NAMES)}


def _cron_field_to_systemd(field: str, lo: int, hi: int, kind: str) -> str:
    """Convert one cron field to a systemd calendar component.

    *kind* is ``"time"`` (minute/hour, steps start at 0, values zero-padded),
    ``"date"`` (dom or month, steps start at 1), or ``"dow"`` (numbers 0-7 or
    three-letter names, mapped to systemd day names).

    Raises :class:`ValueError` for anything that cannot be converted
    **exactly** — a silently wrong schedule is worse than an error.
    """
    step_start = 0 if kind == "time" else 1

    def single(tok: str) -> int:
        if kind == "dow":
            idx = _DOW_INDEX.get(tok.lower())
            if idx is not None:
                return idx
        if not tok.isdigit():
            raise ValueError(f"unsupported cron value {tok!r}")
        v = int(tok)
        if not lo <= v <= hi:
            raise ValueError(f"cron value {tok!r} outside range {lo}-{hi}")
        return v

    def fmt(v: int) -> str:
        if kind == "time":
            return f"{v:02d}"
        return str(v)

    def component(tok: str) -> str:
        if "-" in tok:
            a, _, b = tok.partition("-")
            va, vb = single(a), single(b)
            if va > vb:
                raise ValueError(f"inverted cron range {tok!r}")
            if kind == "dow":
                return f"{_DOW_NAMES[va % 7]}..{_DOW_NAMES[vb % 7]}"
            return f"{fmt(va)}..{fmt(vb)}"
        v = single(tok)
        if kind == "dow":
            return _DOW_NAMES[v % 7]
        return fmt(v)

    if field == "*":
        return "*"
    if field.startswith("*/"):
        step = field[2:]
        if kind == "dow":
            raise ValueError(f"stepped day-of-week ({field!r}) is not supported")
        if not step.isdigit() or int(step) == 0:
            raise ValueError(f"unsupported cron step {field!r}")
        return f"{step_start}/{int(step)}"
    return ",".join(component(tok) for tok in field.split(","))


def _cron_to_oncalendar(cron_expr: str) -> str:
    """Convert a 5-field cron expression to a systemd ``OnCalendar`` value.

    Supported: ``*``, plain values, comma lists, ``a-b`` ranges (all fields)
    and ``*/N`` steps (minute/hour/dom/month). Everything else raises
    :class:`ValueError` — callers must surface that instead of guessing.
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"cron expression must have 5 fields, got {len(parts)}: {cron_expr!r}"
        )
    minute_s, hour_s, dom_s, month_s, dow_s = parts

    minute = _cron_field_to_systemd(minute_s, 0, 59, "time")
    hour = _cron_field_to_systemd(hour_s, 0, 23, "time")
    dom = _cron_field_to_systemd(dom_s, 1, 31, "date")
    month = _cron_field_to_systemd(month_s, 1, 12, "date")
    dow = _cron_field_to_systemd(dow_s, 0, 7, "dow")

    # Date part: leave at *-*-* unless dom/month constrain it.
    if dom == "*" and month == "*":
        date_part = "*-*-*"
    else:
        date_part = f"*-{month}-{dom}"

    # Time part. Seconds default to :00; when a step (a/b) is present we omit
    # the seconds component, matching the documented `*:0/15` form.
    if "/" in minute or "/" in hour:
        time_part = f"{hour}:{minute}"
    else:
        time_part = f"{hour}:{minute}:00"

    prefix = f"{dow} " if dow != "*" else ""
    return f"{prefix}{date_part} {time_part}"


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
    Never returns ``root`` — a system-wide (root-owned) install must not make
    the backup job run as root.
    """
    pm_path = _find_pm_binary()
    user: str | None = None
    try:
        stat_info = os.stat(pm_path)
        import pwd
        user = pwd.getpwuid(stat_info.st_uid).pw_name
    except (OSError, KeyError):
        user = None
    if not user or user == "root":
        user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "ubuntu"
    return user


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

    # Check if timer unit exists and is active (read-only — no sudo needed)
    try:
        status = subprocess.run(
            ["systemctl", "is-active", f"{_SERVICE_NAME}.timer"],
            capture_output=True, text=True, check=False,
        )
        if status.stdout.strip() == "active":
            result["active"] = True
    except Exception:
        pass

    # Read the OnCalendar value from the timer unit (world-readable path)
    if _TIMER_PATH.is_file():
        try:
            content = _TIMER_PATH.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.strip().startswith("OnCalendar="):
                    oncal = line.strip().split("=", 1)[1]
                    result["raw"] = oncal
                    result["schedule"] = _oncalendar_to_human(oncal)
                    break
        except Exception:
            pass

    # Read the command from the service unit (world-readable path)
    if _SERVICE_PATH.is_file():
        try:
            content = _SERVICE_PATH.read_text(encoding="utf-8")
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

    Raises
    ------
    ValueError
        If *schedule* is not a supported 5-field cron expression.
    RuntimeError
        If a systemd command fails (includes stderr for diagnosis).
    """
    if pm_path is None:
        pm_path = _find_pm_binary()

    # Validate before touching anything — a bad expression must fail loudly,
    # not silently produce a wrong schedule.
    oncalendar = _cron_to_oncalendar(schedule)
    user = _get_service_user()

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
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(
            f"systemctl failed (exit {exc.returncode}): {stderr or exc.cmd}"
        ) from exc


def remove_sftp_cron() -> bool:
    """Remove the SFTP backup systemd timer.  Returns ``True`` on success."""
    try:
        _systemctl("stop", f"{_SERVICE_NAME}.timer", check=False)
        _systemctl("disable", f"{_SERVICE_NAME}.timer", check=False)
        _remove_unit_file(_TIMER_PATH)
        _remove_unit_file(_SERVICE_PATH)
        _systemctl("daemon-reload")
        # Also remove any legacy root-crontab entry from the old design so
        # backups don't keep running after being "disabled".
        _cleanup_legacy_crontab()
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
    # *-*-* *:0/15 → every 15 minutes
    step_min = re.match(r'\*-\*-\*\s+\*:(\d+)/(\d+)$', oncalendar)
    if step_min:
        return f"every {step_min.group(2)} minutes"

    # DOW *-*-* HH:MM:SS → weekly on DOW at HH:MM (names, lists or ranges)
    dow_match = re.match(
        r'([A-Z][a-z]{2}(?:(?:\.\.|,)[A-Z][a-z]{2})*)\s+\*-\*-\*\s+(\d{2}:\d{2}):\d{2}',
        oncalendar,
    )
    if dow_match:
        return f"weekly on {dow_match.group(1)} at {dow_match.group(2)}"

    # *-*-D HH:MM:SS → monthly on day D at HH:MM
    month_match = re.match(r'\*-\*-(\d[\d,]*)\s+(\d{2}:\d{2}):\d{2}', oncalendar)
    if month_match:
        return f"monthly on day {month_match.group(1)} at {month_match.group(2)}"

    # *-*-* HH:MM:SS → daily at HH:MM
    daily_match = re.match(r'\*-\*-\*\s+(\d{2}:\d{2}):\d{2}', oncalendar)
    if daily_match:
        return f"daily at {daily_match.group(1)}"

    return oncalendar
