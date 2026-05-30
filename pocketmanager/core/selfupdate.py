"""Self-update mechanism for PocketManager.

Checks for updates by comparing the latest GitHub commit with the locally
installed commit hash (stored in ``_COMMIT_HASH``).  Installs the latest
version via ``pip`` when an update is available.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

# GitHub repository details
_REPO_API = "https://api.github.com/repos/YacineSahli/PocketManager"
_REPO_URL = "git+https://github.com/YacineSahli/PocketManager.git@master"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _install_root() -> Path:
    """Return the project root directory (where ``pyproject.toml`` lives)."""
    return Path(__file__).resolve().parent.parent.parent


def _read_local_commit() -> str:
    """Return the installed commit hash from the marker file, or ``""``."""
    marker = _install_root() / "pocketmanager" / ".commit"
    try:
        return marker.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        # Fallback: try git if the install is from a git clone
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=_install_root(),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return ""


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
    url = (
        f"{_REPO_API}/compare/{local_hash}...{remote_hash}"
    )
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
    """Reinstall PocketManager from the GitHub repository via pip.

    Uses ``--force-reinstall`` and ``--no-cache-dir`` to ensure the latest
    code is fetched and installed, bypassing any pip caching issues.

    Returns ``True`` on success, ``False`` on failure.
    """
    try:
        result = subprocess.run(
            [
                "pip", "install",
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

    # Write the new commit hash to the marker file
    new_hash = _fetch_remote_commit()
    if new_hash:
        marker = _install_root() / "pocketmanager" / "commit.txt"
        try:
            marker.write_text(new_hash + "\n", encoding="utf-8")
        except OSError:
            pass

    return True
