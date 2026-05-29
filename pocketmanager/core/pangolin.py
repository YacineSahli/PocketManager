"""Pangolin reverse-proxy API client.

Provides helpers for creating, deleting, and querying public resources
exposed through the Pangolin dashboard API (e.g. ``apps.yacinesahli.com``).

All functions load configuration internally via :func:`load_config`.

Functions raise :class:`PangolinError` (or a subclass) on failure so callers
can decide how to surface the problem.
"""

from __future__ import annotations

from typing import Any

import requests

from pocketmanager.core.config import load_config


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PangolinError(Exception):
    """Base exception for Pangolin API errors."""


class PangolinConfigError(PangolinError):
    """Raised when Pangolin configuration is incomplete or invalid.

    The ``missing`` attribute contains a list of dot-notation config keys
    that are empty or unset.
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        keys = ", ".join(missing)
        super().__init__(
            f"Pangolin is not fully configured. Missing: {keys}. "
            f"Run 'pm config set <key> <value>' for each."
        )


class PangolinAPIError(PangolinError):
    """Raised when a Pangolin API call fails.

    The ``status_code`` attribute contains the HTTP status (or ``None`` for
    network-level failures).
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


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


def _check_config(
    config: dict[str, Any],
    *,
    require_org_id: bool = False,
    require_domain_id: bool = False,
    require_site_id: bool = False,
) -> None:
    """Validate that required Pangolon config values are set and non-empty.

    Raises :class:`PangolinConfigError` with all missing keys listed.
    """
    missing: list[str] = []
    pangolin_cfg = config.get("pangolin", {})

    if not pangolin_cfg.get("api_url"):
        missing.append("pangolin.api_url")
    if not pangolin_cfg.get("api_key"):
        missing.append("pangolin.api_key")
    if require_org_id and not pangolin_cfg.get("org_id"):
        missing.append("pangolin.org_id")
    if require_domain_id and not pangolin_cfg.get("default_domain_id"):
        missing.append("pangolin.default_domain_id")
    if require_site_id and not pangolin_cfg.get("site_id"):
        missing.append("pangolin.site_id")

    if missing:
        raise PangolinConfigError(missing)


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
) -> dict:
    """Create a new public resource and configure its target.

    Two API calls are made:

    1. **Create the resource** — ``PUT /org/{org_id}/resource``
    2. **Set the target** — ``PUT /resource/{resource_id}/target``

    Returns the resource response dict (which includes ``resourceId``).

    Raises
    ------
    PangolinConfigError
        If required config values are missing.
    PangolinAPIError
        If an API call fails.
    """
    config = load_config()
    _check_config(config, require_org_id=True, require_domain_id=True, require_site_id=True)

    api_url = config.get("pangolin", {}).get("api_url", "")
    headers = _get_api_headers(config)

    # Step 1: Create the resource
    try:
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
    except requests.RequestException as exc:
        raise PangolinAPIError(f"Failed to create Pangolin resource: {exc}") from exc

    if not create_resp.ok:
        raise PangolinAPIError(
            f"Pangolin API returned {create_resp.status_code} when creating resource: "
            f"{create_resp.text[:200]}",
            status_code=create_resp.status_code,
        )

    resource_data = create_resp.json()
    resource_id = resource_data.get("resourceId")

    if not resource_id:
        raise PangolinAPIError(
            "Pangolin API did not return a resourceId in the response.",
        )

    # Step 2: Configure the target
    try:
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
    except requests.RequestException as exc:
        raise PangolinAPIError(
            f"Resource created (id={resource_id}) but failed to set target: {exc}"
        ) from exc

    if not target_resp.ok:
        raise PangolinAPIError(
            f"Resource created (id={resource_id}) but target config returned "
            f"{target_resp.status_code}: {target_resp.text[:200]}",
            status_code=target_resp.status_code,
        )

    return resource_data


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
