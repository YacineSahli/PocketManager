"""PocketBase binary download, version detection, and caching.

All download helpers use only the Python standard library
(:mod:`urllib.request`, :mod:`zipfile`) to avoid external dependencies.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from pocketmanager.core.config import get


# ---------------------------------------------------------------------------
# Architecture / OS detection
# ---------------------------------------------------------------------------

_ARCH_MAP: dict[str, str] = {
    "aarch64": "arm64",
    "x86_64": "amd64",
    "armv7l": "armv7",
}


def detect_arch() -> str:
    """Return the current machine architecture in PocketBase release naming.

    Runs ``uname -m`` and maps the result to one of ``arm64``, ``amd64``, or
    ``armv7``.

    Raises:
        RuntimeError: Architecture is not supported.
    """
    result = subprocess.run(
        ["uname", "-m"],
        capture_output=True,
        text=True,
        check=True,
    )
    raw = result.stdout.strip()
    mapped = _ARCH_MAP.get(raw)
    if mapped is None:
        raise RuntimeError(
            f"Unsupported architecture '{raw}'. "
            f"Supported: {', '.join(sorted(_ARCH_MAP.values()))}"
        )
    return mapped


def detect_os() -> str:
    """Return the target OS string.

    Currently PocketManager only targets Linux.
    """
    return "linux"


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def get_latest_version() -> str:
    """Query the GitHub API for the latest PocketBase release version.

    Returns the version string **without** the ``v`` prefix
    (e.g. ``"0.39.0"``).

    Raises:
        RuntimeError: The API request failed or the response was unexpected.
    """
    url = "https://api.github.com/repos/pocketbase/pocketbase/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to query GitHub API for latest version: {exc}") from exc

    tag_name: str = data.get("tag_name", "")
    if not tag_name:
        raise RuntimeError("GitHub API response missing 'tag_name'")
    # Strip leading 'v' if present
    return tag_name.lstrip("v")


def get_download_url(version: str, arch: str | None = None) -> str:
    """Construct the PocketBase download URL for *version* and *arch*.

    If *arch* is ``None`` the current machine architecture is detected
    automatically.
    """
    if arch is None:
        arch = detect_arch()
    return (
        f"https://github.com/pocketbase/pocketbase/releases/download"
        f"/v{version}/pocketbase_{version}_linux_{arch}.zip"
    )


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_dir() -> Path:
    """Return the cache directory from config."""
    return Path(get("cache_dir", "/home/ubuntu/.pocketmanager/cache"))


def get_cached_binary_path(version: str) -> Path:
    """Return the expected path to the cached PocketBase binary."""
    arch = detect_arch()
    return _cache_dir() / f"pocketbase_{version}_linux_{arch}" / "pocketbase"


def is_version_cached(version: str) -> bool:
    """Check whether the PocketBase binary for *version* is already cached."""
    return get_cached_binary_path(version).is_file()


def download_and_cache(version: str) -> Path:
    """Download a PocketBase release zip and extract it into the cache.

    Returns the path to the extracted ``pocketbase`` binary.

    Raises:
        RuntimeError: Download or extraction failed.
    """
    arch = detect_arch()
    cache = _cache_dir()
    dest_dir = cache / f"pocketbase_{version}_linux_{arch}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    binary_path = dest_dir / "pocketbase"

    if binary_path.is_file():
        # Already cached — nothing to do.
        return binary_path

    url = get_download_url(version, arch)

    # Download to a temporary file
    try:
        fd, tmp_zip = tempfile.mkstemp(suffix=".zip", prefix="pb_download_", dir=cache)
        try:
            with urllib.request.urlopen(url, timeout=300) as resp:
                with open(fd, "wb") as fh:
                    # Stream the download in chunks to keep memory usage low
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        fh.write(chunk)
        except Exception:
            # Clean up partial download
            try:
                Path(tmp_zip).unlink(missing_ok=True)
            except OSError:
                pass
            raise
    finally:
        try:
            import os
            os.close(fd)  # type: ignore[possibly-undefined]
        except OSError:
            pass

    # Extract the zip
    try:
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            zf.extractall(dest_dir)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"Downloaded file is not a valid zip: {exc}") from exc
    finally:
        try:
            Path(tmp_zip).unlink(missing_ok=True)
        except OSError:
            pass

    # Make the binary executable
    binary_path.chmod(0o755)

    if not binary_path.is_file():
        raise RuntimeError(
            f"Expected binary not found after extraction: {binary_path}"
        )

    return binary_path


def ensure_binary(version: str) -> Path:
    """Return the cached binary path, downloading if necessary."""
    if is_version_cached(version):
        return get_cached_binary_path(version)
    return download_and_cache(version)


# ---------------------------------------------------------------------------
# Instance version detection
# ---------------------------------------------------------------------------


def detect_instance_version(instance_dir: Path) -> str | None:
    """Try to detect the PocketBase version from an instance directory.

    Looks for a zip file matching the pattern
    ``pocketbase_<VERSION>_linux_<ARCH>.zip`` and extracts the version.
    Returns ``None`` if the version cannot be determined.
    """
    if not instance_dir.is_dir():
        return None

    # Pattern: pocketbase_0.36.9_linux_arm64.zip
    zip_pattern = re.compile(r"^pocketbase_(\d+\.\d+\.\d+)_linux_\w+\.zip$")
    for entry in instance_dir.iterdir():
        if entry.is_file() and entry.suffix == ".zip":
            match = zip_pattern.match(entry.name)
            if match:
                return match.group(1)
    return None
