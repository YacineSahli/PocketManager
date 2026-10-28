"""Health checking for PocketBase instances.

Provides helpers to probe the ``/api/health`` endpoint of each instance
and aggregate the results.

Functions:
- check_instance_health: probe a single instance by port
- check_all_instances: probe every registered instance
"""

from __future__ import annotations

import time
from typing import Any

import requests

from pocketmanager.core.state import get_all_instances
from pocketmanager.core.systemd import is_active


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_instance_health(port: int) -> dict[str, Any]:
    """Check the health of a single PocketBase instance on *port*.

    Probes ``http://localhost:{port}/api/health`` and measures response time.

    Returns
    -------
    dict
        ``{"healthy": bool, "response_time_ms": float | None, "error": str | None}``
    """
    url = f"http://localhost:{port}/api/health"
    try:
        start = time.monotonic()
        resp = requests.get(url, timeout=5)
        elapsed_ms = (time.monotonic() - start) * 1000

        if resp.status_code == 200:
            return {
                "healthy": True,
                "response_time_ms": round(elapsed_ms, 1),
                "error": None,
            }
        return {
            "healthy": False,
            "response_time_ms": round(elapsed_ms, 1),
            "error": f"HTTP {resp.status_code}",
        }
    except requests.exceptions.ConnectionError as exc:
        return {
            "healthy": False,
            "response_time_ms": None,
            "error": f"Connection refused: {exc}",
        }
    except requests.exceptions.Timeout:
        return {
            "healthy": False,
            "response_time_ms": None,
            "error": "Request timed out",
        }
    except Exception as exc:
        return {
            "healthy": False,
            "response_time_ms": None,
            "error": str(exc),
        }


def check_all_instances() -> list[dict[str, Any]]:
    """Check the health of every registered instance.

    Loads all instances from state, probes each one's health endpoint, and
    also checks whether the systemd service is active.

    Returns
    -------
    list[dict]
        Each dict contains ``name``, ``port``, ``healthy``,
        ``response_time_ms``, ``error``, and ``active``.
    """
    instances = get_all_instances()
    results: list[dict[str, Any]] = []

    for inst in instances:
        name = inst.get("name", "unknown")
        port = inst.get("port", 0)

        health = check_instance_health(port)
        active = is_active(name)

        results.append(
            {
                "name": name,
                "port": port,
                "healthy": health["healthy"],
                "response_time_ms": health["response_time_ms"],
                "error": health["error"],
                "active": active,
            }
        )

    return results
