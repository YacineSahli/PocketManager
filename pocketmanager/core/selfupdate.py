"""Self-update mechanism for PocketManager.

Checks for updates by comparing the installed commit hash (stored in
``commit.txt``) with the latest commit on GitHub.  Installs the latest
version via ``pip install --force-reinstall`` from the git repository URL.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

# GitHub repository details
_REPO_URL = "git+https://github.com/YacineSahli/PocketManager.git@master"
_REPO_API = "https://api.github.com/repos/YacineSahli/PocketManager"

# Marker file lives inside the pocketmanager package directory
_COMMIT_MARKER = Path(__file__).resolve().parent.parent / "commit.txt"


# ---------------------------------------------------------------------------
# Commit tracking
# ---------------------------------------------------------------------------


def _read_local_commit() -> str:
    """Return the installed commit hash from the marker file, or ``""``."""
    try:
        return _COMMIT_MARKER.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


def _write_local_commit(hash_val: str) -> None:
    """Write the commit hash to the marker file."""
    try:
        _COMMIT_MARKER.write_text(hash_val + "\n", encoding="utf-8")
    except OSError:
        pass


def _fetch_remote_commit() -> str:
    """Fetch the latest commit hash on ``master`` from GitHub API."""
    url = f"{_REPO_API}/commits/master"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PocketManager"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("sha", "")
    except Exception:
        return ""


def _fetch_commit_log(local_hash: str, remote_hash: str) -> str:
    """Fetch one-line commit log between two hashes from GitHub API."""
    url = f"{_REPO_API}/compare/{local_hash}...{remote_hash}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PocketManager"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            commits = data.get("commits", [])
            return "\n".join(
                f'{c["sha"][:7]} {c["commit"]["message"].splitlines()[0]}'
                for c in commits
            )
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_current_version() -> str:
    """Return the currently installed PocketManager version."""
    from pocketmanager import __version__  # noqa: WPS433

    return __version__


def check_for_update() -> dict | None:
    """Check whether a newer version is available on GitHub.

    Returns a dict with ``local`` (current short hash), ``remote`` (remote
    short hash), and ``log`` (one-line log of incoming commits) if updates
    are available, or ``None`` if already up to date (or on failure).
    """
    local_hash = _read_local_commit()
    remote_hash = _fetch_remote_commit()

    if not local_hash or not remote_hash:
        return None

    if local_hash == remote_hash:
        return None

    incoming = _fetch_commit_log(local_hash, remote_hash)

    return {
        "local": local_hash[:7],
        "remote": remote_hash[:7],
        "log": incoming,
    }


def perform_update() -> bool:
    """Reinstall PocketManager from GitHub via pip.

    Uses ``--force-reinstall`` and ``--no-cache-dir`` to ensure the latest
    code is fetched and installed, bypassing any pip caching issues.

    Returns ``True`` on success, ``False`` on failure.
    """
    # Guard: refuse to run as root
    if os.geteuid() == 0:
        real_user = os.environ.get("SUDO_USER", "")
        print(
            "Error: refusing to run self-update as root. "
            "Run as your normal user instead."
            f"{f' (try: sudo -u {real_user} pm self-update)' if real_user else ''}"
        )
        return False

    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pip", "install",
                "--user",
                "--break-system-packages",
                "--force-reinstall",
                "--no-cache-dir",
                _REPO_URL,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False

    if result.returncode != 0:
        print(f"pip install failed: {result.stderr.strip()}")
        return False

    # Update commit marker with the new remote hash
    new_hash = _fetch_remote_commit()
    if new_hash:
        _write_local_commit(new_hash)

    return True
