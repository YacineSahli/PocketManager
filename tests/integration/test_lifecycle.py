"""Integration tests for the full PocketBase instance lifecycle.

Tests exercise the real PocketBase binary and API where possible.
Systemd operations (requiring sudo) are mocked at the instance module level.
"""

from __future__ import annotations

import time
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_healthy(port: int, timeout: int = 20) -> bool:
    """Poll the health endpoint until it returns 200 or timeout expires."""
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


def _instance_mocks(fake_binary: Path):
    """Return a context manager that patches all instance-level dependencies.

    This patches the functions imported *into* ``instance.py`` (not the
    original ``systemd.py`` definitions) so that calls from
    ``create_instance()`` etc. hit our mocks.

    Yields a list of mock objects for assertions:
        [0] ensure_binary, [1] get_latest_version, [2] is_port_free,
        [3] ensure_pocketbase_user, [4] set_permissions, [5] set_capabilities,
        [6] create_service, [7] start_service, [8] stop_service,
        [9] restart_service, [10] remove_service, [11] is_active,
        [12] get_status, [13] subprocess.run
    """
    patches = [
        patch("pocketmanager.core.instance.ensure_binary", return_value=fake_binary),
        patch("pocketmanager.core.instance.get_latest_version", return_value="0.25.0"),
        patch("pocketmanager.core.instance.is_port_free", return_value=True),
        patch("pocketmanager.core.instance.ensure_pocketbase_user", return_value=True),
        patch("pocketmanager.core.instance.set_permissions"),
        patch("pocketmanager.core.instance.set_capabilities"),
        patch(
            "pocketmanager.core.instance.create_service",
            return_value=Path("/tmp/test.service"),
        ),
        patch("pocketmanager.core.instance.start_service", return_value=True),
        patch("pocketmanager.core.instance.stop_service", return_value=True),
        patch("pocketmanager.core.instance.restart_service", return_value=True),
        patch("pocketmanager.core.instance.remove_service", return_value=True),
        patch("pocketmanager.core.instance.is_active", return_value=False),
        patch(
            "pocketmanager.core.instance.get_status",
            return_value={
                "active": False,
                "status_text": "unknown",
                "uptime_seconds": None,
                "pid": None,
            },
        ),
        patch("pocketmanager.core.instance.subprocess.run"),
        patch("pocketmanager.core.instance._create_superadmin", return_value=None),
    ]

    class _MockContext:
        def __enter__(self):
            self._stack = ExitStack()
            self.mocks = []
            for p in patches:
                self.mocks.append(self._stack.enter_context(p))
            return self.mocks

        def __exit__(self, *exc):
            return self._stack.__exit__(*exc)

    return _MockContext()


# ---------------------------------------------------------------------------
# 1. Full lifecycle test (real PocketBase)
# ---------------------------------------------------------------------------


def test_full_lifecycle(pb_runner, isolated_env):
    """Exercise the complete lifecycle against a real PocketBase instance.

    Steps: create superuser → health check → authenticate → backup CRUD
    → restore → delete backup → cleanup state.
    """
    from pocketmanager.core import backup as backup_mod
    from pocketmanager.core import health as health_mod
    from pocketmanager.core import state as state_mod

    # -- 1. Register instance in state --
    state_mod.add_instance(
        {
            "name": "inttest",
            "port": pb_runner.port,
            "version": "0.23.0",
            "instance_dir": str(pb_runner.instance_dir),
            "subdomain": None,
            "domain": None,
            "env": {},
            "superadmin_email": "test@test.com",
            "superadmin_password": "testpassword123456",
        }
    )
    instance = state_mod.get_instance("inttest")
    assert instance is not None
    assert instance["port"] == pb_runner.port

    # -- 2. Create superuser --
    result = pb_runner.create_superuser("test@test.com", "testpassword123456")
    assert result is True, "Superuser creation failed"

    # -- 3. Health check --
    health = health_mod.check_instance_health(pb_runner.port)
    assert health["healthy"] is True, f"Instance not healthy: {health.get('error')}"

    # -- 4. Authenticate --
    token = backup_mod.authenticate(
        pb_runner.url, "test@test.com", "testpassword123456"
    )
    assert token is not None, "Authentication failed"
    assert isinstance(token, str), "Token should be a string"

    # -- 5. Create backup --
    created = backup_mod.create_backup(pb_runner.url, auth_token=token)
    assert created is True, "Backup creation failed"

    # -- 6. List backups --
    backups = backup_mod.list_backups(pb_runner.url, auth_token=token)
    assert isinstance(backups, list), "list_backups should return a list"
    assert len(backups) >= 1, "Expected at least one backup"
    first = backups[0]
    assert "key" in first, "Backup entry should have 'key'"
    assert "modified" in first, "Backup entry should have 'modified'"
    assert "size" in first, "Backup entry should have 'size'"

    # -- 7. Download backup --
    backup_key = first["key"]
    dest_path = str(isolated_env["home"] / "downloaded_backup.zip")
    downloaded = backup_mod.download_backup(
        pb_runner.url, backup_key, dest_path, auth_token=token
    )
    assert downloaded is True, "Backup download failed"
    dest_file = Path(dest_path)
    assert dest_file.exists(), "Downloaded backup file should exist"
    assert dest_file.stat().st_size > 0, "Downloaded backup file should not be empty"

    # -- 8. Restore backup --
    restored = backup_mod.restore_backup(
        pb_runner.url, backup_key, auth_token=token
    )
    assert restored is True, "Backup restore failed"

    # PocketBase restarts after restore — wait for it to become healthy again
    assert _wait_healthy(pb_runner.port, timeout=30), (
        "PocketBase did not become healthy after restore"
    )

    # -- 9. Delete backup --
    deleted = backup_mod.delete_backup(
        pb_runner.url, backup_key, auth_token=token
    )
    assert deleted is True, "Backup deletion failed"

    # -- 10. Verify backup is gone --
    backups_after = backup_mod.list_backups(pb_runner.url, auth_token=token)
    keys_after = {b["key"] for b in backups_after}
    assert backup_key not in keys_after, (
        f"Deleted backup {backup_key!r} still appears in list"
    )

    # -- 11. Remove instance from state --
    removed = state_mod.remove_instance("inttest")
    assert removed is not None, "remove_instance returned None"
    assert removed["name"] == "inttest"
    assert state_mod.get_instance("inttest") is None


# ---------------------------------------------------------------------------
# 2. Instance create with mocked systemd
# ---------------------------------------------------------------------------


def test_instance_create_with_mocked_systemd(isolated_env, pocketbase_binary):
    """Test create_instance with mocked systemd operations.

    Verifies name validation, directory creation, binary copy,
    state registration, and that systemd mocks are called.
    """
    from pocketmanager.core import instance as instance_mod
    from pocketmanager.core import state as state_mod

    with _instance_mocks(pocketbase_binary) as mocks:
        result = instance_mod.create_instance(
            "test-create",
            port=9191,
            pangolin=False,
        )

    # Verify result
    assert result is not None
    assert result["name"] == "test-create"
    assert result["port"] == 9191
    assert result["version"] == "0.25.0"
    assert "instance_dir" in result

    # Verify state was persisted
    inst = state_mod.get_instance("test-create")
    assert inst is not None
    assert inst["port"] == 9191

    # Verify directory structure was created
    instance_dir = Path(inst["instance_dir"])
    assert (instance_dir / "pb_data").is_dir()
    assert (instance_dir / "pb_hooks").is_dir()
    assert (instance_dir / "pb_migrations").is_dir()
    assert (instance_dir / "pocketbase").is_file()

    # Verify key mocks were called
    mocks[3].assert_called_once()   # ensure_pocketbase_user
    mocks[4].assert_called_once()   # set_permissions
    mocks[5].assert_called_once()   # set_capabilities
    mocks[6].assert_called_once()   # create_service
    mocks[7].assert_called_once()   # start_service


# ---------------------------------------------------------------------------
# 3. Instance remove with mocked systemd
# ---------------------------------------------------------------------------


def test_instance_remove_with_mocked_systemd(isolated_env, pocketbase_binary):
    """Test remove_instance after creating one with mocked systemd.

    Verifies state removal and that stop/remove service mocks are called.
    """
    from pocketmanager.core import instance as instance_mod
    from pocketmanager.core import state as state_mod

    with _instance_mocks(pocketbase_binary):
        instance_mod.create_instance("test-remove", port=9192, pangolin=False)

    assert state_mod.get_instance("test-remove") is not None

    with _instance_mocks(pocketbase_binary) as mocks:
        removed = instance_mod.remove_instance("test-remove")

    assert removed is not None
    assert removed["name"] == "test-remove"
    assert state_mod.get_instance("test-remove") is None

    # stop_service and remove_service should have been called
    mocks[8].assert_called_with("test-remove")   # stop_service
    mocks[10].assert_called_with("test-remove")   # remove_service


# ---------------------------------------------------------------------------
# 4. Instance list
# ---------------------------------------------------------------------------


def test_instance_list(isolated_env, pocketbase_binary):
    """Test list_instances returns all registered instances."""
    from pocketmanager.core import instance as instance_mod

    with _instance_mocks(pocketbase_binary):
        instance_mod.create_instance("list-alpha", port=9200, pangolin=False)
        instance_mod.create_instance("list-beta", port=9201, pangolin=False)

    instances = instance_mod.list_instances()
    names = {i["name"] for i in instances}
    assert "list-alpha" in names
    assert "list-beta" in names


# ---------------------------------------------------------------------------
# 5. Instance status / info
# ---------------------------------------------------------------------------


def test_instance_status(isolated_env, pocketbase_binary):
    """Test get_instance_info returns expected keys and realistic values."""
    from pocketmanager.core import instance as instance_mod

    with _instance_mocks(pocketbase_binary):
        instance_mod.create_instance("info-test", port=9210, pangolin=False)

    info = instance_mod.get_instance_info("info-test")

    # Expected state keys
    assert info["name"] == "info-test"
    assert info["port"] == 9210
    assert "instance_dir" in info
    assert "version" in info
    assert "created_at" in info

    # Runtime info keys
    assert "active" in info
    assert "uptime_seconds" in info
    assert "health" in info
    assert "disk_usage" in info
    assert "backup_count" in info

    # Since the service isn't really running, health should be False
    assert info["health"] is False


# ---------------------------------------------------------------------------
# 6. Instance start / stop / restart
# ---------------------------------------------------------------------------


def test_instance_start_stop_restart(isolated_env, pocketbase_binary):
    """Test start_instance, stop_instance, and restart_instance.

    Verifies the corresponding mocks are called.
    """
    from pocketmanager.core import instance as instance_mod

    with _instance_mocks(pocketbase_binary) as mocks:
        instance_mod.create_instance("svc-test", port=9220, pangolin=False)

        # Reset call history
        mocks[7].reset_mock()   # start_service
        mocks[8].reset_mock()   # stop_service
        mocks[9].reset_mock()   # restart_service

        # -- Start --
        started = instance_mod.start_instance("svc-test")
        assert started is True
        mocks[7].assert_called_once_with("svc-test")

        # -- Stop --
        stopped = instance_mod.stop_instance("svc-test")
        assert stopped is True
        mocks[8].assert_called_once_with("svc-test")

        # -- Restart --
        restarted = instance_mod.restart_instance("svc-test")
        assert restarted is True
        mocks[9].assert_called_once_with("svc-test")


# ---------------------------------------------------------------------------
# 7. Backup auto-backup config
# ---------------------------------------------------------------------------


def test_backup_auto_backup_config(pb_runner, isolated_env):
    """Test configuring automatic backups via the PocketBase settings API.

    Creates a superuser, authenticates, then sets auto-backup config.
    """
    from pocketmanager.core import backup as backup_mod

    # Create superuser
    result = pb_runner.create_superuser("test@test.com", "testpassword123456")
    assert result is True, "Superuser creation failed"

    # Authenticate
    token = backup_mod.authenticate(
        pb_runner.url, "test@test.com", "testpassword123456"
    )
    assert token is not None, "Authentication failed"

    # Configure auto-backup
    configured = backup_mod.configure_auto_backup(
        pb_runner.url,
        cron="0 3 * * *",
        max_keep=5,
        auth_token=token,
    )
    assert configured is True, "Auto-backup configuration failed"
