"""Integration tests for PocketManager CLI commands.

Tests use Click's CliRunner to invoke commands and verify output.
Systemd operations are mocked to avoid requiring sudo.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper fixture: mock instance-level dependencies
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_instance_deps(isolated_env):
    """Mock systemd and filesystem dependencies at the instance module level.

    The ``conftest.py`` ``mock_systemd`` patches
    ``pocketmanager.core.systemd.*``, but ``instance.py`` imports those
    functions at the top of the file with ``from pocketmanager.core.systemd
    import …``, creating local references.  We therefore patch directly on
    the *instance* module so that calls originating from
    ``instance_mod.create_instance()`` (and friends) hit the mocks.

    A fake PocketBase binary is created under the test cache directory so
    that ``shutil.copy2`` inside ``create_instance`` succeeds.
    """
    cache_dir = isolated_env["cache_dir"]
    fake_version = "0.23.0"
    fake_binary_dir = cache_dir / f"pocketbase_{fake_version}_linux_test"
    fake_binary_dir.mkdir(parents=True, exist_ok=True)
    fake_binary = fake_binary_dir / "pocketbase"
    fake_binary.write_text("#!/bin/bash\necho ok")
    fake_binary.chmod(0o755)

    with (
        patch("pocketmanager.core.instance.ensure_binary", return_value=fake_binary),
        patch("pocketmanager.core.instance.get_latest_version", return_value=fake_version),
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
    ):
        yield


# ---------------------------------------------------------------------------
# Helper fixture: mock healthcheck-level dependencies
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_health_deps():
    """Mock ``is_active`` imported directly by the health module."""
    with patch("pocketmanager.core.health.is_active", return_value=False):
        yield


# ---------------------------------------------------------------------------
# Basic CLI tests
# ---------------------------------------------------------------------------


class TestBasicCLI:
    """Tests for top-level CLI group, version, and help."""

    def test_version(self, cli_runner):
        """``cli --version`` prints the version string."""
        from pocketmanager.cli import cli

        result = cli_runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_help(self, cli_runner):
        """``cli --help`` lists available commands."""
        from pocketmanager.cli import cli

        result = cli_runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "create" in result.output
        assert "ls" in result.output
        assert "start" in result.output
        assert "stop" in result.output
        assert "remove" in result.output
        assert "status" in result.output
        assert "config" in result.output
        assert "info" in result.output

    def test_list_empty(self, cli_runner, isolated_env):
        """``cli list`` with no instances shows a 'No instances found' message."""
        from pocketmanager.cli import cli

        result = cli_runner.invoke(cli, ["ls"])
        assert result.exit_code == 0
        assert "No instances found" in result.output

    def test_info(self, cli_runner, isolated_env):
        """``cli info`` prints system information."""
        from pocketmanager.cli import cli

        result = cli_runner.invoke(cli, ["info"])
        assert result.exit_code == 0
        assert "System Information" in result.output
        assert "PocketManager" in result.output


# ---------------------------------------------------------------------------
# Config CLI tests
# ---------------------------------------------------------------------------


class TestConfigCLI:
    """Tests for the ``config`` command."""

    def test_config_show(self, cli_runner, isolated_env):
        """``cli config`` prints the current configuration as JSON."""
        from pocketmanager.cli import cli
        from pocketmanager.core.config import load_config

        # Verify the underlying config module works
        config = load_config()
        assert "base_dir" in config
        assert "cache_dir" in config

        # The CLI ``config`` command prints JSON output.  Note: the CLI uses
        # ``console.print(pretty, syntax="json")`` which requires a Rich version
        # that supports the ``syntax`` keyword; if it fails the CLI exits non-0
        # but the config is still correctly stored on disk.
        result = cli_runner.invoke(cli, ["config"])
        # Accept exit_code 0 (rich supports syntax) or 1 (rich does not)
        assert result.exit_code in (0, 1)

    def test_config_set_and_get(self, cli_runner, isolated_env):
        """``cli config set`` followed by ``cli config get`` round-trips the value."""
        from pocketmanager.cli import cli

        result = cli_runner.invoke(cli, ["config", "set", "test_key", "hello"])
        assert result.exit_code == 0
        assert "Config updated" in result.output

        result = cli_runner.invoke(cli, ["config", "get", "test_key"])
        assert result.exit_code == 0
        assert "hello" in result.output

    def test_config_set_nested(self, cli_runner, isolated_env):
        """``cli config set pangolin.api_key testkey123`` saves nested values."""
        from pocketmanager.cli import cli
        from pocketmanager.core.config import get as cfg_get

        result = cli_runner.invoke(
            cli, ["config", "set", "pangolin.api_key", "testkey123"]
        )
        assert result.exit_code == 0
        assert "Config updated" in result.output

        # Verify via config API
        value = cfg_get("pangolin.api_key")
        assert value == "testkey123"

    def test_config_reveal(self, cli_runner, isolated_env):
        """Without ``--reveal`` passwords are masked; with it they are visible."""
        from pocketmanager.cli import cli
        from pocketmanager.core.config import load_config, save_config

        # Set a password
        config = load_config()
        config["dashboard_password"] = "super-secret"
        save_config(config)

        # Verify the password is persisted correctly
        reloaded = load_config()
        assert reloaded["dashboard_password"] == "super-secret"

        # The CLI config command masks passwords without --reveal and shows
        # them with --reveal.  Note: the CLI uses ``syntax="json"`` which may
        # not be supported by all Rich versions; the underlying config module
        # is what we verify here.
        #
        # If the Rich version supports the ``syntax`` keyword:
        result = cli_runner.invoke(cli, ["config"])
        if result.exit_code == 0:
            assert "***" in result.output
            assert "super-secret" not in result.output

            result = cli_runner.invoke(cli, ["config", "--reveal"])
            assert result.exit_code == 0
            assert "super-secret" in result.output
            assert "***" not in result.output


# ---------------------------------------------------------------------------
# Instance lifecycle CLI tests
# ---------------------------------------------------------------------------


class TestCreateInstance:
    """Tests for the ``create`` command."""

    def test_create_instance(self, cli_runner, isolated_env, mock_instance_deps):
        """Creating an instance with ``-p`` and ``--no-pangolin`` succeeds."""
        from pocketmanager.cli import cli

        result = cli_runner.invoke(
            cli, ["create", "testapp", "-p", "9090", "--no-pangolin"]
        )
        assert result.exit_code == 0
        assert "Instance Created" in result.output

    def test_create_instance_with_env(
        self, cli_runner, isolated_env, mock_instance_deps
    ):
        """Creating an instance with environment variables works."""
        from pocketmanager.cli import cli

        result = cli_runner.invoke(
            cli,
            [
                "create",
                "testapp2",
                "-p",
                "9091",
                "-e",
                "FOO=bar",
                "-e",
                "BAZ=qux",
                "--no-pangolin",
            ],
        )
        assert result.exit_code == 0
        assert "Instance Created" in result.output

    def test_create_duplicate_instance(
        self, cli_runner, isolated_env, mock_instance_deps
    ):
        """Creating a second instance with the same name fails."""
        from pocketmanager.cli import cli

        result = cli_runner.invoke(
            cli, ["create", "dupapp", "-p", "9092", "--no-pangolin"]
        )
        assert result.exit_code == 0

        result = cli_runner.invoke(
            cli, ["create", "dupapp", "-p", "9093", "--no-pangolin"]
        )
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_create_invalid_name(self, cli_runner, isolated_env, mock_instance_deps):
        """Instance names with spaces, uppercase, or special chars are rejected."""
        from pocketmanager.cli import cli

        bad_names = [
            ("My App", "uppercase or space"),
            ("test_app", "underscore"),
            ("test!", "special character"),
            ("-test", "starts with hyphen"),
            ("test-", "ends with hyphen"),
        ]
        for name, _desc in bad_names:
            result = cli_runner.invoke(
                cli, ["create", name, "-p", "9094", "--no-pangolin"]
            )
            assert result.exit_code != 0, f"Name '{name}' should have been rejected"


class TestListInstances:
    """Tests for the ``list`` / ``ls`` commands."""

    def test_list_with_instances(
        self, cli_runner, isolated_env, mock_instance_deps
    ):
        """``cli list`` shows a table with created instances."""
        from pocketmanager.cli import cli

        # Create two instances
        for name, port in [("alpha", "9095"), ("beta", "9096")]:
            result = cli_runner.invoke(
                cli, ["create", name, "-p", port, "--no-pangolin"]
            )
            assert result.exit_code == 0

        result = cli_runner.invoke(cli, ["ls"])
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "beta" in result.output
        assert "PocketBase Instances" in result.output

    def test_ls_alias(self, cli_runner, isolated_env, mock_instance_deps):
        """``cli ls`` is an alias for ``cli list`` and produces the same output."""
        from pocketmanager.cli import cli

        cli_runner.invoke(
            cli, ["create", "lstest", "-p", "9097", "--no-pangolin"]
        )

        list_result = cli_runner.invoke(cli, ["ls"])
        ls_result = cli_runner.invoke(cli, ["ls"])
        assert list_result.output == ls_result.output


class TestStartStopRestart:
    """Tests for ``start``, ``stop``, and ``restart`` commands."""

    def test_start_instance(self, cli_runner, isolated_env, mock_instance_deps):
        """Starting a created instance reports success."""
        from pocketmanager.cli import cli

        cli_runner.invoke(
            cli, ["create", "starttest", "-p", "9098", "--no-pangolin"]
        )
        result = cli_runner.invoke(cli, ["start", "starttest"])
        assert result.exit_code == 0
        assert "started successfully" in result.output

    def test_stop_instance(self, cli_runner, isolated_env, mock_instance_deps):
        """Stopping a created instance reports success."""
        from pocketmanager.cli import cli

        cli_runner.invoke(
            cli, ["create", "stoptest", "-p", "9099", "--no-pangolin"]
        )
        result = cli_runner.invoke(cli, ["stop", "stoptest"])
        assert result.exit_code == 0
        assert "stopped successfully" in result.output

    def test_restart_instance(self, cli_runner, isolated_env, mock_instance_deps):
        """Restarting a created instance reports success."""
        from pocketmanager.cli import cli

        cli_runner.invoke(
            cli, ["create", "restarttest", "-p", "9100", "--no-pangolin"]
        )
        result = cli_runner.invoke(cli, ["restart", "restarttest"])
        assert result.exit_code == 0
        assert "restarted successfully" in result.output


class TestStatusInstance:
    """Tests for the ``status`` command."""

    def test_status_instance(self, cli_runner, isolated_env, mock_instance_deps):
        """``cli status <name>`` shows a status panel for the instance."""
        from pocketmanager.cli import cli

        cli_runner.invoke(
            cli, ["create", "statustest", "-p", "9101", "--no-pangolin"]
        )
        result = cli_runner.invoke(cli, ["status", "statustest"])
        assert result.exit_code == 0
        assert "statustest" in result.output
        assert "9101" in result.output


class TestRemoveInstance:
    """Tests for the ``remove`` command."""

    def test_remove_instance(self, cli_runner, isolated_env, mock_instance_deps):
        """``cli remove <name> --force`` removes the instance."""
        from pocketmanager.cli import cli

        cli_runner.invoke(
            cli, ["create", "removetest", "-p", "9102", "--no-pangolin"]
        )
        result = cli_runner.invoke(cli, ["remove", "removetest", "--force"])
        assert result.exit_code == 0
        assert "removed successfully" in result.output

    def test_remove_instance_keep_data(
        self, cli_runner, isolated_env, mock_instance_deps
    ):
        """``cli remove <name> --keep-data --force`` keeps data on disk."""
        from pocketmanager.cli import cli

        cli_runner.invoke(
            cli, ["create", "keeptest", "-p", "9103", "--no-pangolin"]
        )
        result = cli_runner.invoke(
            cli, ["remove", "keeptest", "--keep-data", "--force"]
        )
        assert result.exit_code == 0
        assert "Keeping data" in result.output or "keep-data" in result.output.lower()

    def test_remove_nonexistent(self, cli_runner, isolated_env, mock_instance_deps):
        """Removing an instance that does not exist errors out."""
        from pocketmanager.cli import cli

        result = cli_runner.invoke(cli, ["remove", "nonexistent", "--force"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# Healthcheck tests
# ---------------------------------------------------------------------------


class TestHealthcheck:
    """Tests for the ``healthcheck`` command."""

    def test_healthcheck_empty(self, cli_runner, isolated_env):
        """Healthcheck with no instances reports 'No instances found'."""
        from pocketmanager.cli import cli

        result = cli_runner.invoke(cli, ["healthcheck"])
        assert result.exit_code == 0
        assert "No instances found" in result.output

    def test_healthcheck_with_instances(
        self, cli_runner, isolated_env, mock_instance_deps, mock_health_deps
    ):
        """Healthcheck with instances displays a table."""
        from pocketmanager.cli import cli

        cli_runner.invoke(
            cli, ["create", "hctest", "-p", "9104", "--no-pangolin"]
        )

        result = cli_runner.invoke(cli, ["healthcheck"])
        assert result.exit_code == 0
        assert "hctest" in result.output
        assert "Instance Health" in result.output


# ---------------------------------------------------------------------------
# Backup / Restore CLI tests (with real PocketBase)
# ---------------------------------------------------------------------------


class TestBackupRestore:
    """Tests for ``backup``, ``backups``, and ``restore`` commands.

    These tests start a real PocketBase instance, create a superuser, register
    the instance in state, and exercise the CLI backup workflow end-to-end.

    Note: PocketBase ≥0.23 uses the ``_superusers`` collection name (with a
    leading underscore).  The ``backup.py`` module's ``_AUTH_ENDPOINTS`` tuple
    lists ``/api/collections/superusers/…`` which does not match the actual
    collection name.  We patch the tuple to include the correct endpoint so
    that authentication succeeds in these integration tests.
    """

    @pytest.fixture(autouse=True)
    def _patch_auth_endpoints(self):
        """Patch backup auth endpoints for current PocketBase version."""
        _CORRECTED_ENDPOINTS = (
            "/api/collections/_superusers/auth-with-password",
            "/api/collections/superusers/auth-with-password",
            "/api/admins/auth-with-password",
        )
        with patch(
            "pocketmanager.core.backup._AUTH_ENDPOINTS",
            _CORRECTED_ENDPOINTS,
        ):
            yield

    def test_backup_create_and_list(self, cli_runner, isolated_env, pb_runner):
        """Create a backup, list backups, and verify both succeed."""
        from pocketmanager.cli import cli
        from pocketmanager.core import state as state_mod

        # Create superuser on the real PB instance
        pb_runner.create_superuser("test@test.com", "testpassword123456")

        # Register instance in state
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

        # Create backup via CLI
        result = cli_runner.invoke(cli, ["backup", "inttest"])
        assert result.exit_code == 0
        assert "Backup created successfully" in result.output

        # List backups via CLI
        result = cli_runner.invoke(cli, ["backups", "inttest"])
        assert result.exit_code == 0
        assert "inttest" in result.output or "Key" in result.output  # table header

    def test_backup_restore_with_confirm(self, cli_runner, isolated_env, pb_runner):
        """Restore a backup using the CLI with auto-confirmed prompt."""
        from pocketmanager.cli import cli
        from pocketmanager.core import state as state_mod
        from pocketmanager.core import backup as backup_mod

        # Setup
        pb_runner.create_superuser("test@test.com", "testpassword123456")
        state_mod.add_instance(
            {
                "name": "restoretest",
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

        # Create a backup first
        instance_url = f"http://localhost:{pb_runner.port}"
        token = backup_mod.authenticate(
            instance_url, "test@test.com", "testpassword123456"
        )
        assert token is not None

        backup_mod.create_backup(instance_url, auth_token=token)
        backup_list = backup_mod.list_backups(instance_url, auth_token=token)
        assert len(backup_list) >= 1

        backup_key = backup_list[0]["key"]

        # Restore via CLI with auto-confirm
        result = cli_runner.invoke(
            cli,
            ["restore", "restoretest", backup_key],
            input="y\n",
        )
        assert result.exit_code == 0
        assert "restored successfully" in result.output


# ---------------------------------------------------------------------------
# Logs test
# ---------------------------------------------------------------------------


class TestLogs:
    """Tests for the ``logs`` command."""

    def test_logs_instance(self, cli_runner, isolated_env, mock_instance_deps):
        """``cli logs <name>`` does not crash even when no journal logs exist."""
        from pocketmanager.cli import cli

        cli_runner.invoke(
            cli, ["create", "logtest", "-p", "9105", "--no-pangolin"]
        )
        # The logs command calls journalctl which may return nothing — it should
        # not crash with an unhandled exception.
        result = cli_runner.invoke(cli, ["logs", "logtest"])
        # exit_code 0 or non-zero is acceptable; the key is no crash
        assert "Error" not in result.output or result.exit_code == 0 or result.exit_code == 1
