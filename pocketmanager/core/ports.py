"""Port allocation and conflict detection for PocketBase instances.

Provides helpers to query the system's listening ports, inspect ports already
reserved in the instance state file, and find a free port within the configured
range.
"""

from __future__ import annotations

import re
import socket
import subprocess
from typing import Any

from pocketmanager.core.config import get
from pocketmanager.core.state import get_all_instances


# ---------------------------------------------------------------------------
# System port query
# ---------------------------------------------------------------------------


def get_used_ports() -> set[int]:
    """Return all TCP ports currently in LISTEN state on this host.

    Runs ``ss -tlnp`` via :mod:`subprocess` and parses the output.  Ports are
    returned as a :class:`set` of :class:`int`.
    """
    ports: set[int] = set()
    try:
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        # If ``ss`` is unavailable or fails, return an empty set so callers
        # still work (they will also check state-file ports).
        return ports

    # Example ss line:
    #   LISTEN 0 4096 0.0.0.0:8101 0.0.0.0:*
    for line in result.stdout.splitlines():
        # We only care about LISTEN lines
        if "LISTEN" not in line:
            continue
        parts = line.split()
        for part in parts:
            # Look for the local address column (contains a colon)
            if ":" in part:
                # The local address is the last colon-separated value
                port_str = part.rsplit(":", 1)[-1]
                try:
                    ports.add(int(port_str))
                except ValueError:
                    continue
    return ports


# ---------------------------------------------------------------------------
# State-file port query
# ---------------------------------------------------------------------------


def get_allocated_ports() -> set[int]:
    """Return all ports reserved in ``instances.json``."""
    ports: set[int] = set()
    for inst in get_all_instances():
        port = inst.get("port")
        if isinstance(port, int):
            ports.add(port)
    return ports


# ---------------------------------------------------------------------------
# Availability helpers
# ---------------------------------------------------------------------------


def _try_bind(port: int) -> bool:
    """Attempt to bind to *port* to verify it is truly free.

    Returns ``True`` if the bind succeeds, ``False`` otherwise.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            return True
    except OSError:
        return False


def is_port_free(port: int) -> bool:
    """Check whether *port* is free on both the system **and** in state."""
    return port not in get_used_ports() and port not in get_allocated_ports()


def find_available_port(
    start: int | None = None,
    end: int | None = None,
) -> int:
    """Find the next free TCP port in the given range.

    If *start* / *end* are not supplied the configured ``port_range.min`` and
    ``port_range.max`` values are used (falling back to 8090–8999).

    The port must be free both on the system (``ss``) **and** in the state file.

    Raises:
        RuntimeError: No free port found in the requested range.
    """
    if start is None:
        start = int(get("port_range.min", 8090))
    if end is None:
        end = int(get("port_range.max", 8999))

    used = get_used_ports()
    allocated = get_allocated_ports()
    occupied = used | allocated

    for port in range(start, end + 1):
        if port not in occupied and _try_bind(port):
            return port

    raise RuntimeError(
        f"No free port available in range {start}–{end} "
        f"(system: {sorted(used)}, state: {sorted(allocated)})"
    )
