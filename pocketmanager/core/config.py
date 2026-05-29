"""Global configuration management for PocketManager.

Config is stored in ``~/.config/pocketmanager/`` (or ``POCKETMANAGER_HOME``).
Resolution order for the config directory:

1. ``POCKETMANAGER_HOME`` environment variable
2. ``~/.config/pocketmanager/``
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict[str, Any] = {
    "base_dir": "/home/ubuntu/pocketbases",
    "cache_dir": "/home/ubuntu/.pocketmanager/cache",
    "dashboard_port": 8888,
    "dashboard_password": "",
    "dashboard_pangolin_resource_id": "",
    "port_range": {"min": 8090, "max": 8999},
    "pangolin": {
        "dashboard_url": "https://apps.yacinesahli.com",
        "api_url": "https://api.apps.yacinesahli.com/v1",
        "api_key": "",
        "org_id": "",
        "default_domain_id": "",
        "default_domain": "",
        "subdomain_suffix": "",
        "site_id": "",
        "target_ip": "172.19.0.1",
    },
    "defaults": {
        "auto_backups_enabled": True,
        "auto_backups_cron": "0 3 * * *",
        "auto_backups_max_keep": 7,
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict that is *base* merged with *override* (recursive)."""
    merged = base.copy()
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_default_config() -> dict[str, Any]:
    """Return the full default configuration structure."""
    import copy

    return copy.deepcopy(_DEFAULT_CONFIG)


def get_config_dir() -> Path:
    """Return the directory that contains ``config.json``.

    Resolution order:

    1. ``POCKETMANAGER_HOME`` environment variable
    2. ``~/.config/pocketmanager/``
    """
    env_home = os.environ.get("POCKETMANAGER_HOME")
    if env_home:
        return Path(env_home).resolve()

    return Path.home() / ".config" / "pocketmanager"


def get_config_path() -> Path:
    """Return the path to ``config.json``."""
    return get_config_dir() / "config.json"


def load_config() -> dict[str, Any]:
    """Load config from disk, merging with defaults.

    Returns the default config verbatim when the file does not exist.
    """
    path = get_config_path()
    if path.is_file():
        with path.open("r", encoding="utf-8") as fh:
            on_disk: dict[str, Any] = json.load(fh)
        return _deep_merge(get_default_config(), on_disk)
    return get_default_config()


def save_config(config: dict[str, Any]) -> None:
    """Atomically write *config* to ``config.json``.

    Uses a temporary file + rename to avoid corrupt state on crash.
    """
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to a temp file in the same directory so rename stays on the
    # same filesystem (atomic on POSIX).
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix="config_",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        # os.replace is atomic on the same filesystem
        os.replace(tmp_path, path)
        # Restrict to owner-only to protect secrets (api_key, dashboard_password)
        os.chmod(path, 0o600)
    except BaseException:
        # Clean up the temp file if anything went wrong
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get(key: str, default: Any = None) -> Any:
    """Get a nested config value using **dot notation**.

    Example::

        get("pangolin.api_key")
    """
    config = load_config()
    parts = key.split(".")
    node: Any = config
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def set(key: str, value: Any) -> None:
    """Set a nested config value using **dot notation** and auto-save.

    Example::

        set("pangolin.api_key", "secret")
    """
    config = load_config()
    parts = key.split(".")
    node: Any = config
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value
    save_config(config)
