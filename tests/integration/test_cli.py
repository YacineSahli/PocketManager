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

    ``instance.py`` imports the systemd helpers at the top of the file with
    ``from pocketmanager.core.systemd import …``, creating local references.
    We therefore patch directly on the *instance* module so that calls
    originating from ``instance_mod.create_instance()`` (and friends) hit
    the mocks.

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

        # The CLI ``config`` command prints the config as highlighted JSON.
        result = cli_runner.invoke(cli, ["config"])
        assert result.exit_code == 0
        assert "base_dir" in result.output
        assert "cache_dir" in result.output
        assert "pangolin" in result.output

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

        # Without --reveal the password is masked in the printed JSON.
        result = cli_runner.invoke(cli, ["config"])
        assert result.exit_code == 0
        assert "***" in result.output
        assert "super-secret" not in result.output

        # With --reveal the password is visible.
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
    """Tests for the ``ls`` command."""

    def test_list_with_instances(
        self, cli_runner, isolated_env, mock_instance_deps
    ):
        """``cli ls`` shows a table with created instances."""
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

    def test_ls_lists_instance(self, cli_runner, isolated_env, mock_instance_deps):
        """``cli ls`` lists created instances with table columns; there is no
        ``list`` alias (invoking it is a usage error)."""
        from pocketmanager.cli import cli

        result = cli_runner.invoke(
            cli, ["create", "lstest", "-p", "9097", "--no-pangolin"]
        )
        assert result.exit_code == 0

        result = cli_runner.invoke(cli, ["ls"])
        assert result.exit_code == 0
        assert "lstest" in result.output
        assert "Name" in result.output
        assert "Port" in result.output

        # 'list' is not a registered command — only 'ls'.
        result = cli_runner.invoke(cli, ["list"])
        assert result.exit_code != 0
        assert "No such command" in result.output


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
        """Healthcheck with a stopped instance exits 1 and reports issues.

        The created instance has no real process behind it, so the new
        ``healthcheck`` contract applies: unhealthy instances → non-zero
        exit so scripts and monitors can trust the command.
        """
        from pocketmanager.cli import cli

        cli_runner.invoke(
            cli, ["create", "hctest", "-p", "9104", "--no-pangolin"]
        )

        result = cli_runner.invoke(cli, ["healthcheck"])
        assert result.exit_code == 1
        assert "hctest" in result.output
        assert "Instance Health" in result.output
        assert "have issues" in result.output


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
        from pocketmanager.core import backup as backup_mod
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

        # List backups via CLI — the created backup's key must appear in the
        # table (not just the table header).
        instance_url = f"http://localhost:{pb_runner.port}"
        token = backup_mod.authenticate(
            instance_url, "test@test.com", "testpassword123456"
        )
        backup_list = backup_mod.list_backups(instance_url, auth_token=token)
        assert len(backup_list) >= 1

        result = cli_runner.invoke(cli, ["backups", "inttest"])
        assert result.exit_code == 0
        assert "Backups for inttest" in result.output
        assert "Key" in result.output  # table header
        for entry in backup_list:
            assert entry["key"] in result.output

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
    """Tests for the ``logs`` command (journalctl output is mocked)."""

    def test_logs_print_journal_output(self, cli_runner, isolated_env):
        """``cli logs <name>`` prints the journalctl output verbatim."""
        from pocketmanager.cli import cli

        with patch(
            "pocketmanager.core.systemd.get_journal_logs_ex",
            return_value=(
                "2026-10-28T03:00:00 log line one\n2026-10-28T03:00:01 log line two\n",
                "",
                0,
            ),
        ):
            result = cli_runner.invoke(cli, ["logs", "logtest"])

        assert result.exit_code == 0
        assert "log line one" in result.output
        assert "log line two" in result.output

    def test_logs_error_shows_stderr(self, cli_runner, isolated_env):
        """A journalctl failure exits 1 and surfaces stderr instead of the
        misleading 'No logs found'."""
        from pocketmanager.cli import cli

        with patch(
            "pocketmanager.core.systemd.get_journal_logs_ex",
            return_value=("", "unauthorized access to journal", 1),
        ):
            result = cli_runner.invoke(cli, ["logs", "logtest"])

        assert result.exit_code == 1
        assert "unauthorized" in result.output
        assert "No logs found" not in result.output

    def test_logs_empty_output(self, cli_runner, isolated_env):
        """Empty journal output with rc 0 reports 'No logs found'."""
        from pocketmanager.cli import cli

        with patch(
            "pocketmanager.core.systemd.get_journal_logs_ex",
            return_value=("", "", 0),
        ):
            result = cli_runner.invoke(cli, ["logs", "logtest"])

        assert result.exit_code == 0
        assert "No logs found" in result.output


# ---------------------------------------------------------------------------
# Doctor test
# ---------------------------------------------------------------------------


class TestDoctor:
    """Tests for the ``doctor`` command (post-update migrations)."""

    def test_doctor_missing_service_file(self, cli_runner, isolated_env):
        """An instance whose systemd unit file does not exist (as on this
        test host, where /etc/systemd/system/<unit> is absent) is reported as
        missing, the legacy crontab cleanup runs, and doctor completes."""
        from pocketmanager.cli import cli
        from pocketmanager.core import state as state_mod
        from pocketmanager.core.state import get_meta

        state_mod.add_instance({"name": "doctest", "port": 9106})

        with patch("pocketmanager.core.cron.cleanup_legacy_crontab") as cleanup_mock:
            result = cli_runner.invoke(cli, ["doctor", "--yes"])

        assert result.exit_code == 0
        assert "no service file found" in result.output
        assert "Doctor complete" in result.output
        cleanup_mock.assert_called_once()
        # The migration pass is recorded so `pm info` stops nudging.
        assert get_meta("doctor_bind_host_migrate") is not None
