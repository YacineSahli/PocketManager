"""Self-update mechanism for PocketManager.

Detects the installation method (venv editable install vs. pip --user) and
updates accordingly.  The recommended install uses a git clone + venv at
``$HOME/pocketmanager/``, so self-update does a ``git pull`` followed by
``pip install -e .`` inside the venv.

For non-venv installs, falls back to ``pip install --force-reinstall``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

# GitHub repository details
_REPO_URL = "https://github.com/YacineSahli/PocketManager.git"
_REPO_API = "https://api.github.com/repos/YacineSahli/PocketManager"


# ---------------------------------------------------------------------------
# Installation detection
# ---------------------------------------------------------------------------


def _package_dir() -> Path:
    """Return the directory containing the pocketmanager package."""
    return Path(__file__).resolve().parent.parent  # pocketmanager/core/ → pocketmanager/


def _is_venv() -> bool:
    """Return True if running inside a virtual environment."""
    return sys.prefix != sys.base_prefix


def _is_editable_install() -> bool:
    """Return True if installed in editable (develop) mode.

    Detected by checking if the clone directory (parent of the package)
    contains a ``.git`` folder.
    """
    clone = _package_dir().parent
    return (clone / ".git").is_dir()


def _venv_root() -> Path | None:
    """Return the venv root (contains ``bin/``, ``lib/``), or None."""
    if _is_venv():
        return Path(sys.prefix)
    return None


def _install_clone_dir() -> Path | None:
    """Return the git clone directory for the install, or None.

    For editable installs, this is the package directory's parent
    (since pocketmanager/ is inside the clone).
    """
    if _is_editable_install():
        return _package_dir().parent
    return None


# ---------------------------------------------------------------------------
# Commit tracking
# ---------------------------------------------------------------------------


def _read_local_commit() -> str:
    """Return the installed commit hash from the marker file, or ``""``."""
    marker = _package_dir() / "commit.txt"
    try:
        return marker.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        # Fallback: read from git if it's an editable install
        clone = _install_clone_dir()
        if clone:
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=clone,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except Exception:
                pass
        return ""


def _write_local_commit(hash_val: str) -> None:
    """Write the commit hash to the marker file."""
    marker = _package_dir() / "commit.txt"
    try:
        marker.write_text(hash_val + "\n", encoding="utf-8")
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
    """Update PocketManager.

    Detects the installation method and uses the appropriate strategy:

    - **Venv + editable install** (recommended): ``git pull`` in the clone
      directory, then ``pip install -e .`` using the venv's pip.
    - **System/user pip install**: ``pip install --force-reinstall`` from
      the git repo URL.

    Returns ``True`` on success, ``False`` on failure.
    """
    # Guard: refuse to run as root
    if os.geteuid() == 0:
        real_user = os.environ.get("SUDO_USER", "")
        print(
            f"Error: refusing to run self-update as root. "
            f"Run as your normal user instead."
            f"{f' (try: sudo -u {real_user} pm self-update)' if real_user else ''}"
        )
        return False

    venv = _venv_root()
    clone = _install_clone_dir()

    if venv and clone:
        return _update_venv_editable(venv, clone)

    return _update_pip_user()


def _update_venv_editable(venv: Path, clone: Path) -> bool:
    """Update a venv-based editable install: git pull + pip install -e ."""
    pip = venv / "bin" / "pip"

    # Step 1: git pull
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=clone,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"git pull failed: {exc}")
        return False

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "unrelated histories" in stderr:
            print(
                "git pull failed: unrelated histories detected.\n"
                "The remote repository was likely force-pushed.\n"
                f"Fix: rm -rf {clone} && pm self-update  (will re-clone)\n"
                f"Or re-run the installer: bash <(curl -fsSL ...)"
            )
        else:
            print(f"git pull failed: {stderr}")
        return False

    print(result.stdout.strip())

    # Step 2: pip install -e . (in the venv)
    try:
        result = subprocess.run(
            [str(pip), "install", "-e", str(clone)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"pip install failed: {exc}")
        return False

    if result.returncode != 0:
        print(f"pip install failed: {result.stderr.strip()}")
        return False

    # Step 3: update commit marker
    new_hash = _fetch_remote_commit()
    if new_hash:
        _write_local_commit(new_hash)

    return True


def _update_pip_user() -> bool:
    """Fallback: reinstall from GitHub via pip."""
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pip", "install",
                "--user",
                "--break-system-packages",
                "--force-reinstall",
                "--no-cache-dir",
                f"git+{_REPO_URL}@master",
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

    # Update commit marker
    new_hash = _fetch_remote_commit()
    if new_hash:
        _write_local_commit(new_hash)

    return True
