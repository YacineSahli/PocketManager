"""Self-update mechanism for PocketManager.

Checks for new releases on GitHub and optionally downloads and installs the
updated version over the existing installation, preserving user data and
the virtual environment.

The install root is determined by walking up from this file to the project
root (where ``pyproject.toml`` lives).
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

GITHUB_REPO = "yacinesahli/PocketManager"


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version string like ``"1.2.3"`` into a comparable tuple.

    Strips an optional leading ``v`` and ignores any trailing suffix after
    ``-`` or ``+``.
    """
    clean = version_str.lstrip("v")
    if "-" in clean:
        clean = clean.split("-")[0]
    if "+" in clean:
        clean = clean.split("+")[0]
    parts: list[int] = []
    for part in clean.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            break
    return tuple(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_current_version() -> str:
    """Return the currently installed PocketManager version."""
    from pocketmanager import __version__  # noqa: WPS433

    return __version__


def get_latest_release() -> dict | None:
    """Fetch the latest GitHub release metadata.

    Returns a dict with ``tag_name``, ``name``, ``body``, ``tarball_url``,
    and ``published_at`` on success, or ``None`` on failure.
    """
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        req = urllib.request.Request(url)
        # Use a short timeout and identify ourselves
        req.add_header("User-Agent", "PocketManager-selfupdate")
        with urllib.request.urlopen(req, timeout=15) as resp:
            import json

            data: dict = json.loads(resp.read().decode("utf-8"))
        return {
            "tag_name": data.get("tag_name", ""),
            "name": data.get("name", ""),
            "body": data.get("body", ""),
            "tarball_url": data.get("tarball_url", ""),
            "published_at": data.get("published_at", ""),
        }
    except Exception:
        return None


def check_for_update() -> dict | None:
    """Compare the current version with the latest GitHub release.

    Returns the release dict if a newer version is available, or ``None``
    if the installation is already up to date (or on failure).
    """
    release = get_latest_release()
    if release is None:
        return None

    current = _parse_version(get_current_version())
    latest = _parse_version(release["tag_name"])

    if latest > current:
        return release

    return None


def perform_update(release: dict | None = None) -> bool:
    """Download and install an updated version of PocketManager.

    Workflow:

    1. If *release* is ``None``, call :func:`check_for_update`.  If still
       ``None``, print a message and return ``True`` (already up to date).
    2. Download the release tarball to a temporary directory.
    3. Extract the archive.
    4. Locate the ``pocketmanager`` package directory inside the archive.
    5. Copy files over the existing installation, preserving ``config.json``,
       ``instances.json``, and ``.venv/``.
    6. Print progress messages throughout.

    Returns ``True`` on success, ``False`` on failure.
    """
    if release is None:
        release = check_for_update()

    if release is None:
        print("Already up to date.")
        return True

    tag = release.get("tag_name", "unknown")
    tarball_url = release.get("tarball_url", "")

    if not tarball_url:
        print("Error: no tarball URL found in release metadata.")
        return False

    print(f"Updating to {tag} ...")

    # Determine the project root (where pyproject.toml lives)
    install_root = Path(__file__).resolve().parent.parent.parent

    # Directories / files to preserve during the update
    preserve = {".venv", "config.json", "instances.json"}

    try:
        # --- Download -------------------------------------------------------
        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_path = Path(tmp_dir) / "update.tar.gz"
            print(f"Downloading from {tarball_url} ...")

            req = urllib.request.Request(tarball_url)
            req.add_header("User-Agent", "PocketManager-selfupdate")
            with urllib.request.urlopen(req, timeout=120) as resp, archive_path.open("wb") as fh:
                shutil.copyfileobj(resp, fh)

            print("Download complete. Extracting ...")

            # --- Extract ----------------------------------------------------
            extract_dir = Path(tmp_dir) / "extracted"
            extract_dir.mkdir()

            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(path=extract_dir, filter="data")

            # --- Find the pocketmanager package inside the archive ---------
            pkg_dir: Path | None = None
            for candidate in extract_dir.rglob("pocketmanager"):
                if candidate.is_dir() and (candidate / "__init__.py").exists():
                    pkg_dir = candidate
                    break

            if pkg_dir is None:
                print("Error: could not find pocketmanager package in archive.")
                return False

            # --- Find the archive root (parent of pocketmanager/) ----------
            archive_root = pkg_dir.parent

            # --- Copy files over the existing installation ------------------
            for item in archive_root.iterdir():
                if item.name in preserve:
                    continue

                dest = install_root / item.name

                # Remove existing destination first
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()

                # Copy the new version
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

        print(f"Successfully updated to {tag}.")
        return True

    except Exception as exc:
        print(f"Error during update: {exc}")
        return False
