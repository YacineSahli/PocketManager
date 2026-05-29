"""Health checking for PocketBase instances.

Provides helpers to probe the ``/api/health`` endpoint of each instance
and aggregate results into a human-readable table.

Functions:
- check_instance_health: probe a single instance by port
- check_all_instances: probe every registered instance
- format_health_table: render results as a formatted table
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


def format_health_table(results: list[dict[str, Any]]) -> str:
    """Render health-check results as a formatted table.

    Uses the **rich** library when available for a nicer table; otherwise
    falls back to a plain-text alignment.
    """
    if not results:
        return "No instances found."

    # ---- Try rich first ---------------------------------------------------
    try:
        from rich.console import Console
        from rich.table import Table

        table = Table(title="Instance Health")
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Port", justify="right", style="magenta")
        table.add_column("Active", justify="center")
        table.add_column("Healthy", justify="center")
        table.add_column("Response (ms)", justify="right")
        table.add_column("Error")

        for row in results:
            active_str = "[green]yes[/green]" if row["active"] else "[dim]no[/dim]"
            healthy_str = "[green]yes[/green]" if row["healthy"] else "[red]no[/red]"
            resp_str = str(row["response_time_ms"]) if row["response_time_ms"] is not None else "-"
            error_str = row["error"] or ""

            table.add_row(
                row["name"],
                str(row["port"]),
                active_str,
                healthy_str,
                resp_str,
                error_str,
            )

        console = Console()
        with console.capture() as capture:
            console.print(table)
        return capture.get()

    except Exception:
        pass

    # ---- Plain-text fallback ----------------------------------------------
    header = (
        f"{'NAME':<20} {'PORT':>6} {'ACTIVE':>8} {'HEALTHY':>8} {'RESP(ms)':>10}  ERROR"
    )
    separator = "-" * len(header)
    lines = [header, separator]

    for row in results:
        active_str = "yes" if row["active"] else "no"
        healthy_str = "yes" if row["healthy"] else "no"
        resp_str = str(row["response_time_ms"]) if row["response_time_ms"] is not None else "-"
        error_str = row["error"] or ""
        lines.append(
            f"{row['name']:<20} {row['port']:>6} {active_str:>8} {healthy_str:>8} {resp_str:>10}  {error_str}"
        )

    return "\n".join(lines)
