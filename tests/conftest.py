"""Shared fixtures for PocketManager integration tests.

Key design decisions:
- Uses POCKETMANAGER_HOME env var to isolate config/state per test
- Downloads PocketBase binary once per session (expensive)
- Runs PocketBase as a subprocess (no systemd/sudo required)
- Mocks systemd module functions for instance creation tests
- Uses a high port range (9090-9999) to avoid conflicts
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from pocketmanager.core import config as config_mod
from pocketmanager.core import instance as instance_mod
from pocketmanager.core import ports as ports_mod
from pocketmanager.core import state as state_mod
from pocketmanager.core import systemd as systemd_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Find a free TCP port by binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def _wait_for_health(port: int, timeout: float = 30) -> bool:
    """Wait for PocketBase health endpoint to return 200."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"http://localhost:{port}/api/health")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Isolated test environment (function-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch):
    """Create an isolated PocketManager environment for each test.

    Sets POCKETMANAGER_HOME, creates config.json with temp paths,
    and returns a dict with all paths.
    """
    home = tmp_path / "pm_home"
    base_dir = tmp_path / "instances"
    cache_dir = tmp_path / "cache"
    home.mkdir()
    base_dir.mkdir()
    cache_dir.mkdir()

    monkeypatch.setenv("POCKETMANAGER_HOME", str(home))

    config = {
        "base_dir": str(base_dir),
        "cache_dir": str(cache_dir),
        "dashboard_port": 18888,
        "dashboard_password": "",
        "port_range": {"min": 9090, "max": 9999},
        "pangolin": {
            "dashboard_url": "",
            "api_url": "",
            "api_key": "",
            "org_id": "",
            "default_domain_id": "",
            "default_domain": "",
            "subdomain_suffix": "",
            "site_id": "",
            "target_ip": "127.0.0.1",
        },
        "defaults": {
            "auto_backups_enabled": True,
            "auto_backups_cron": "0 3 * * *",
            "auto_backups_max_keep": 7,
        },
    }
    (home / "config.json").write_text(json.dumps(config, indent=2))

    return {
        "home": home,
        "base_dir": base_dir,
        "cache_dir": cache_dir,
        "config": config,
    }


# ---------------------------------------------------------------------------
# PocketBase binary (session-scoped — download once)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pocketbase_binary(tmp_path_factory):
    """Download and cache the PocketBase binary for the test session."""
    dest = tmp_path_factory.mktemp("pb_binary")
    binary_path = dest / "pocketbase"

    if not binary_path.exists():
        # Detect architecture
        result = subprocess.run(
            ["uname", "-m"], capture_output=True, text=True, check=True
        )
        arch_map = {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "armv7"}
        arch = arch_map.get(result.stdout.strip(), "amd64")

        # Get latest version from GitHub
        req = urllib.request.Request(
            "https://api.github.com/repos/pocketbase/pocketbase/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        version = data["tag_name"].lstrip("v")

        # Download zip
        url = (
            f"https://github.com/pocketbase/pocketbase/releases/download"
            f"/v{version}/pocketbase_{version}_linux_{arch}.zip"
        )
        zip_path = dest / "pocketbase.zip"
        urllib.request.urlretrieve(url, str(zip_path))

        # Extract
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            zf.extractall(str(dest))
        binary_path.chmod(0o755)
        zip_path.unlink()

    assert binary_path.exists(), f"PocketBase binary not found at {binary_path}"
    return binary_path


# ---------------------------------------------------------------------------
# PocketBase subprocess runner
# ---------------------------------------------------------------------------


class PocketBaseRunner:
    """Manages a real PocketBase subprocess for integration testing."""

    def __init__(self, binary_path: Path, instance_dir: Path, port: int):
        self.binary_path = binary_path
        self.instance_dir = instance_dir
        self.port = port
        self.process: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def start(self, timeout: float = 30) -> None:
        """Start PocketBase and wait until healthy."""
        self.instance_dir.mkdir(parents=True, exist_ok=True)
        for subdir in ("pb_data", "pb_hooks", "pb_migrations"):
            (self.instance_dir / subdir).mkdir(exist_ok=True)

        self.process = subprocess.Popen(
            [str(self.binary_path), "serve", f"--http=0.0.0.0:{self.port}"],
            cwd=str(self.instance_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if not _wait_for_health(self.port, timeout):
            # Collect stderr for diagnostics
            stderr = ""
            if self.process:
                try:
                    _, stderr = self.process.communicate(timeout=2)
                    stderr = stderr.decode("utf-8", errors="replace")
                except Exception:
                    pass
            self.stop()
            raise RuntimeError(
                f"PocketBase did not become healthy within {timeout}s.\n"
                f"stderr: {stderr}"
            )

    def stop(self) -> None:
        """Terminate the PocketBase subprocess."""
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process = None

    def is_healthy(self) -> bool:
        """Check if the PocketBase health endpoint responds with 200."""
        try:
            req = urllib.request.Request(f"{self.url}/api/health")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def create_superuser(self, email: str, password: str) -> bool:
        """Create a superuser via the PocketBase CLI.

        Tries the v0.23+ command first, then falls back to the legacy command.
        """
        # v0.23+: pocketbase superuser upsert <email> <password>
        result = subprocess.run(
            [str(self.binary_path), "superuser", "upsert", email, password],
            cwd=str(self.instance_dir),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return True

        # Legacy: pocketbase admin create <email> <password>
        result = subprocess.run(
            [str(self.binary_path), "admin", "create", email, password],
            cwd=str(self.instance_dir),
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0


@pytest.fixture()
def pb_runner(pocketbase_binary, isolated_env):
    """Create a PocketBaseRunner that starts a real PB instance.

    The runner is started automatically. It is stopped on teardown.
    """
    port = _find_free_port()
    instance_dir = isolated_env["base_dir"] / "pocketbase-inttest"
    runner = PocketBaseRunner(pocketbase_binary, instance_dir, port)
    runner.start()
    yield runner
    runner.stop()


# ---------------------------------------------------------------------------
# Systemd mocking fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_systemd():
    """Patch all systemd functions that require sudo/root.

    Returns a dict of mock objects for assertions:
        - user_mock, perms_mock, caps_mock
        - create_svc_mock, start_svc_mock, stop_svc_mock
        - restart_svc_mock, remove_svc_mock, active_mock, status_mock
    """
    mocks = {}

    # System user
    mocks["user_mock"] = MagicMock(return_value=True)
    # Permissions
    mocks["perms_mock"] = MagicMock()
    # Capabilities
    mocks["caps_mock"] = MagicMock()
    # Service creation
    mocks["create_svc_mock"] = MagicMock(
        return_value=Path("/etc/systemd/system/pocketbase-test.service")
    )
    # Service control
    mocks["start_svc_mock"] = MagicMock(return_value=True)
    mocks["stop_svc_mock"] = MagicMock(return_value=True)
    mocks["restart_svc_mock"] = MagicMock(return_value=True)
    mocks["remove_svc_mock"] = MagicMock(return_value=True)
    # Status
    mocks["active_mock"] = MagicMock(return_value=False)
    mocks["status_mock"] = MagicMock(
        return_value={
            "active": False,
            "status_text": "unknown",
            "uptime_seconds": None,
            "pid": None,
        }
    )

    patches = [
        patch.object(systemd_mod, "ensure_pocketbase_user", mocks["user_mock"]),
        patch.object(systemd_mod, "set_permissions", mocks["perms_mock"]),
        patch.object(systemd_mod, "set_capabilities", mocks["caps_mock"]),
        patch.object(systemd_mod, "create_service", mocks["create_svc_mock"]),
        patch.object(systemd_mod, "start_service", mocks["start_svc_mock"]),
        patch.object(systemd_mod, "stop_service", mocks["stop_svc_mock"]),
        patch.object(systemd_mod, "restart_service", mocks["restart_svc_mock"]),
        patch.object(systemd_mod, "remove_service", mocks["remove_svc_mock"]),
        patch.object(systemd_mod, "is_active", mocks["active_mock"]),
        patch.object(systemd_mod, "get_status", mocks["status_mock"]),
        patch.object(instance_mod, "_create_superadmin", return_value=None),
    ]

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield mocks


# Re-export contextlib.ExitStack for the mock_systemd fixture
from contextlib import ExitStack


# ---------------------------------------------------------------------------
# Click CLI runner
# ---------------------------------------------------------------------------


@pytest.fixture()
def cli_runner():
    """Return a Click CliRunner for testing CLI commands."""
    return CliRunner()
