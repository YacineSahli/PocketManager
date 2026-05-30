"""Self-update mechanism for PocketManager.

Compares the local git commit with the remote GitHub repository and installs
the latest version via ``pip`` when an update is available.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# GitHub repository for PocketManager
_REPO_URL = "git+https://github.com/YacineSahli/PocketManager.git@master"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _install_root() -> Path:
    """Return the project root directory (where ``pyproject.toml`` lives)."""
    return Path(__file__).resolve().parent.parent.parent


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd or _install_root(),
        capture_output=True,
        text=True,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_current_version() -> str:
    """Return the currently installed PocketManager version."""
    from pocketmanager import __version__  # noqa: WPS433

    return __version__


def check_for_update() -> dict | None:
    """Check whether a newer version is available on the remote.

    Runs ``git fetch`` then compares the local ``HEAD`` with
    ``origin/master``.

    Returns a dict with ``local`` (current short hash), ``remote`` (remote
    short hash), and ``log`` (one-line log of incoming commits) if updates
    are available, or ``None`` if already up to date (or on failure).
    """
    root = _install_root()

    fetch = _git("fetch", cwd=root)
    if fetch.returncode != 0:
        return None

    # Compare local HEAD with remote
    rev = _git("rev-parse", "HEAD", "origin/master", cwd=root)
    if rev.returncode != 0:
        return None

    hashes = rev.stdout.strip().splitlines()
    if len(hashes) < 2:
        return None

    local_hash, remote_hash = hashes[0], hashes[1]

    if local_hash == remote_hash:
        return None

    # Get the short hashes + incoming commit log
    short = _git("rev-parse", "--short", "HEAD", "origin/master", cwd=root)
    short_hashes = short.stdout.strip().splitlines() if short.returncode == 0 else (local_hash[:7], remote_hash[:7])

    log = _git("log", "--oneline", f"{local_hash}..{remote_hash}", cwd=root)
    incoming = log.stdout.strip() if log.returncode == 0 else ""

    return {
        "local": short_hashes[0] if len(short_hashes) > 0 else local_hash[:7],
        "remote": short_hashes[1] if len(short_hashes) > 1 else remote_hash[:7],
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

    return True
