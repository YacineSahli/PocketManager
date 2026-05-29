"""Pangolin reverse-proxy API client.

Provides helpers for creating, deleting, and querying public resources
exposed through the Pangolin dashboard API (e.g. ``apps.yacinesahli.com``).

All functions load configuration internally via :func:`load_config` and
wrap HTTP calls in ``try / except`` so they never propagate network errors.
"""

from __future__ import annotations

from typing import Any

import requests

from pocketmanager.core.config import load_config


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_api_headers(config: dict[str, Any]) -> dict[str, str]:
    """Return HTTP headers with Bearer-token authorization for the Pangolin API."""
    api_key = config.get("pangolin", {}).get("api_key", "")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_resource(
    name: str,
    subdomain: str | None,
    domain_id: str,
    org_id: str,
    site_id: int,
    target_ip: str,
    target_port: int,
) -> dict | None:
    """Create a new public resource and configure its target.

    Two API calls are made:

    1. **Create the resource** — ``PUT /org/{org_id}/resource``
    2. **Set the target** — ``PUT /resource/{resource_id}/target``

    Returns the resource response dict (which includes ``resourceId``), or
    ``None`` on any failure.
    """
    config = load_config()
    api_url = config.get("pangolin", {}).get("api_url", "")
    headers = _get_api_headers(config)

    if not api_url or not headers.get("Authorization", "").split()[-1]:
        return None

    try:
        # Step 1: Create the resource
        create_resp = requests.put(
            f"{api_url}/org/{org_id}/resource",
            json={
                "name": name,
                "http": True,
                "protocol": "tcp",
                "domainId": domain_id,
                "subdomain": subdomain or None,
            },
            headers=headers,
            timeout=15,
        )
        create_resp.raise_for_status()

        resource_data = create_resp.json()
        resource_id = resource_data.get("resourceId")

        if not resource_id:
            return None

        # Step 2: Configure the target
        target_resp = requests.put(
            f"{api_url}/resource/{resource_id}/target",
            json={
                "siteId": site_id,
                "ip": target_ip,
                "port": target_port,
                "method": "http",
            },
            headers=headers,
            timeout=15,
        )
        target_resp.raise_for_status()

        return resource_data

    except Exception:
        return None


def delete_resource(resource_id: int) -> bool:
    """Delete a Pangolin resource by its *resource_id*.

    Returns ``True`` on success, ``False`` on any failure.
    """
    config = load_config()
    api_url = config.get("pangolin", {}).get("api_url", "")
    headers = _get_api_headers(config)

    if not api_url:
        return False

    try:
        resp = requests.delete(
            f"{api_url}/resource/{resource_id}",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def list_domains(org_id: str) -> list[dict]:
    """List all domains available in the given organisation.

    Returns a list of domain objects, or an empty list on failure.
    """
    config = load_config()
    api_url = config.get("pangolin", {}).get("api_url", "")
    headers = _get_api_headers(config)

    if not api_url:
        return []

    try:
        resp = requests.get(
            f"{api_url}/org/{org_id}/domains",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []
