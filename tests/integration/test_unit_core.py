"""Unit-style integration tests for core modules with all side effects mocked.

Covers:

* systemd unit generation / env sanitization / bind_host-aware regeneration
* the instance orchestrator's update flow (skip, rollback, no-rollback) and
  create flow (auto-backup wiring, partial-failure cleanup)
* the cron → OnCalendar converter and the SFTP backup timer units
* SFTP upload/prune against a fake paramiko client
* backup API error paths (HTTP errors, connection errors)

None of these tests may touch the real system: no sudo, no systemd, no
network.  Everything that would shell out is patched.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import requests


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_create_deps(isolated_env):
    """Patch the instance module's imported dependencies for create flows.

    ``instance.py`` imports the systemd/pocketbase helpers at the top of the
    file, so the mocks must be installed on the *instance* module (the same
    pattern as ``mock_instance_deps`` in ``test_cli.py``).  A fake cached
    PocketBase binary is created so ``shutil.copy2`` inside ``create_instance``
    succeeds.

    Yields a dict exposing the ``subprocess.run`` recorder for assertions.
    """
    fake_version = "0.23.0"
    fake_binary_dir = isolated_env["cache_dir"] / f"pocketbase_{fake_version}_linux_test"
    fake_binary_dir.mkdir(parents=True, exist_ok=True)
    fake_binary = fake_binary_dir / "pocketbase"
    fake_binary.write_text("#!/bin/sh\necho ok")
    fake_binary.chmod(0o755)

    with (
        patch("pocketmanager.core.instance.ensure_binary", return_value=fake_binary),
        patch("pocketmanager.core.instance.get_latest_version", return_value=fake_version),
        patch("pocketmanager.core.instance.is_port_free", return_value=True),
        patch("pocketmanager.core.instance.find_available_port", return_value=9121),
        patch("pocketmanager.core.instance.ensure_pocketbase_user", return_value=True),
        patch("pocketmanager.core.instance.set_permissions"),
        patch("pocketmanager.core.instance.set_capabilities"),
        patch(
            "pocketmanager.core.instance.create_service",
            return_value=Path("/tmp/test.service"),
        ),
        patch("pocketmanager.core.instance.start_service", return_value=True),
        patch("pocketmanager.core.instance.subprocess.run") as run_mock,
        patch(
            "pocketmanager.core.instance._create_superadmin",
            return_value=("admin@pocketbase.local", "secretpw"),
        ),
    ):
        yield {"subprocess_run": run_mock}


@pytest.fixture()
def update_env(isolated_env):
    """Register an instance in state with a fake instance dir and binaries."""
    from pocketmanager.core import state as state_mod

    instance_dir = isolated_env["base_dir"] / "pocketbase-updtest"
    instance_dir.mkdir()
    (instance_dir / "pocketbase").write_text("fake-binary-0.23")
    (instance_dir / "pocketbase.bak").write_text("fake-binary-old")
    state_mod.add_instance(
        {
            "name": "updtest",
            "port": 9120,
            "version": "0.23.0",
            "instance_dir": str(instance_dir),
        }
    )
    return {
        "instance_dir": instance_dir,
        "binary": instance_dir / "pocketbase",
        "backup": instance_dir / "pocketbase.bak",
    }


# ---------------------------------------------------------------------------
# systemd.generate_service_content / _sanitize_env
# ---------------------------------------------------------------------------


class TestGenerateServiceContent:
    """Tests for systemd unit file generation."""

    def test_renders_core_directives(self):
        """The unit contains ExecStart, User, WorkingDirectory, ReadWritePaths
        and one quoted Environment line per env var."""
        from pocketmanager.core.systemd import generate_service_content

        content = generate_service_content(
            "myapp",
            8090,
            "/srv/pocketbase-myapp",
            env={"FOO": "bar", "BAZ": "qux"},
        )

        assert (
            "ExecStart=/srv/pocketbase-myapp/pocketbase serve "
            "--http=127.0.0.1:8090" in content
        )
        assert "User=pocketbase" in content
        assert "WorkingDirectory=/srv/pocketbase-myapp" in content
        assert "ReadWritePaths=/srv/pocketbase-myapp/pb_data" in content
        assert 'Environment="FOO=bar"' in content
        assert 'Environment="BAZ=qux"' in content

    def test_bind_host_honored(self):
        """A custom bind_host appears in ExecStart; the default does not."""
        from pocketmanager.core.systemd import generate_service_content

        content = generate_service_content(
            "myapp", 8090, "/srv/pocketbase-myapp", bind_host="0.0.0.0"
        )

        assert "ExecStart=/srv/pocketbase-myapp/pocketbase serve --http=0.0.0.0:8090" in content
        assert "--http=127.0.0.1" not in content

    def test_empty_env_no_environment_lines(self):
        """With no env vars the unit contains no Environment= lines."""
        from pocketmanager.core.systemd import generate_service_content

        for env in (None, {}):
            content = generate_service_content("myapp", 8090, "/srv/d", env=env)
            assert "Environment=" not in content

    @pytest.mark.parametrize(
        "env",
        [
            {"K=X": "v"},   # '=' in key
            {'K"V': "v"},   # double quote in key
            {"K'V": "v"},   # single quote in key
            {"K\nV": "v"},  # newline in key
            {"K\\V": "v"},  # backslash in key
            {"K": 'a"b'},   # double quote in value
            {"K": "a'b"},   # single quote in value
            {"K": "a\nb"},  # newline in value
            {"K": "a\\b"},  # backslash in value
            {"": "v"},      # empty key
        ],
        ids=[
            "eq-in-key", "dquote-key", "squote-key", "newline-key",
            "backslash-key", "dquote-value", "squote-value", "newline-value",
            "backslash-value", "empty-key",
        ],
    )
    def test_sanitize_env_rejects_bad_input(self, env):
        """Malformed keys/values raise ValueError (quote-injection guard)."""
        from pocketmanager.core.systemd import _sanitize_env

        with pytest.raises(ValueError):
            _sanitize_env(env)

    def test_sanitize_env_passes_valid_through(self):
        """Valid env dicts are returned unchanged."""
        from pocketmanager.core.systemd import _sanitize_env

        env = {"FOO": "bar", "EMPTY": ""}
        assert _sanitize_env(env) == env


# ---------------------------------------------------------------------------
# systemd.modify_service
# ---------------------------------------------------------------------------


class TestModifyService:
    """Tests for regenerating an existing unit file."""

    def test_modify_service_uses_configured_bind_host(self, isolated_env):
        """modify_service() rewrites a 0.0.0.0 unit to the *configured*
        bind_host (never to a hard-coded default)."""
        from pocketmanager.core import config as config_mod
        from pocketmanager.core import systemd as systemd_mod

        config_mod.set("bind_host", "10.1.2.3")

        # A service file currently bound to 0.0.0.0 (pre-migration state).
        service_file = isolated_env["home"] / "pocketbase-bindtest.service"
        service_file.write_text(
            "[Unit]\n"
            "Description=PocketBase - bindtest\n"
            "\n"
            "[Service]\n"
            "WorkingDirectory=/srv/pocketbase-bindtest\n"
            "ExecStart=/srv/pocketbase-bindtest/pocketbase serve --http=0.0.0.0:8090\n"
        )

        with (
            patch(
                "pocketmanager.core.systemd.get_service_path",
                return_value=service_file,
            ),
            patch("pocketmanager.core.systemd.subprocess.run") as run_mock,
        ):
            result_path = systemd_mod.modify_service("bindtest")

        assert result_path == service_file

        tee_calls = [
            c for c in run_mock.call_args_list
            if c.args and c.args[0][:2] == ["sudo", "tee"]
        ]
        assert tee_calls, "expected a sudo tee write of the regenerated unit"
        new_content = tee_calls[0].kwargs["input"].decode("utf-8")

        assert (
            "ExecStart=/srv/pocketbase-bindtest/pocketbase serve "
            "--http=10.1.2.3:8090" in new_content
        )
        assert "--http=0.0.0.0" not in new_content


# ---------------------------------------------------------------------------
# instance.update_instance
# ---------------------------------------------------------------------------


class TestUpdateInstance:
    """Tests for the update flow: skip, rollback, and no-rollback paths."""

    def test_update_skipped_when_current(self, update_env):
        """Updating to the already-installed version skips everything."""
        from pocketmanager.core import instance as instance_mod
        from pocketmanager.core.state import get_instance

        with (
            patch(
                "pocketmanager.core.instance.get_latest_version",
                return_value="0.23.0",
            ),
            patch("pocketmanager.core.instance.stop_service") as stop_mock,
            patch("pocketmanager.core.instance.start_service") as start_mock,
            patch("pocketmanager.core.instance.ensure_binary") as ensure_mock,
        ):
            record = instance_mod.update_instance("updtest")

        assert record["skipped"] is True
        assert record["version"] == "0.23.0"
        stop_mock.assert_not_called()
        start_mock.assert_not_called()
        ensure_mock.assert_not_called()
        assert get_instance("updtest")["version"] == "0.23.0"

    def test_update_rolls_back_on_failed_start(self, update_env):
        """A failed post-update start restores the .bak binary and reports it."""
        from pocketmanager.core import instance as instance_mod
        from pocketmanager.core.state import get_instance

        cached = update_env["instance_dir"] / "cached-pb"
        cached.write_text("fake-binary-0.24")

        with (
            patch(
                "pocketmanager.core.instance.get_latest_version",
                return_value="0.24.0",
            ),
            patch(
                "pocketmanager.core.instance.ensure_binary", return_value=cached
            ),
            patch("pocketmanager.core.instance.stop_service"),
            patch("pocketmanager.core.instance.set_permissions"),
            patch("pocketmanager.core.instance.set_capabilities"),
            patch(
                "pocketmanager.core.instance.start_service",
                side_effect=[False, True],  # update start fails, rollback start OK
            ),
            patch("pocketmanager.core.instance.subprocess.run") as run_mock,
        ):
            with pytest.raises(RuntimeError) as excinfo:
                instance_mod.update_instance("updtest")

        assert "Rolled back" in str(excinfo.value)

        commands = [c.args[0] for c in run_mock.call_args_list if c.args]
        restore_cmd = [
            "sudo", "cp", "-p",
            str(update_env["backup"]), str(update_env["binary"]),
        ]
        assert restore_cmd in commands, f"rollback cp missing from {commands}"

        # State was not advanced to the new version.
        assert get_instance("updtest")["version"] == "0.23.0"

    def test_update_failed_start_without_rollback(self, update_env):
        """When even the rollback start fails, the error has no rollback note."""
        from pocketmanager.core import instance as instance_mod
        from pocketmanager.core.state import get_instance

        cached = update_env["instance_dir"] / "cached-pb"
        cached.write_text("fake-binary-0.24")

        with (
            patch(
                "pocketmanager.core.instance.get_latest_version",
                return_value="0.24.0",
            ),
            patch(
                "pocketmanager.core.instance.ensure_binary", return_value=cached
            ),
            patch("pocketmanager.core.instance.stop_service"),
            patch("pocketmanager.core.instance.set_permissions"),
            patch("pocketmanager.core.instance.set_capabilities"),
            patch(
                "pocketmanager.core.instance.start_service", return_value=False
            ),
            patch("pocketmanager.core.instance.subprocess.run"),
        ):
            with pytest.raises(RuntimeError) as excinfo:
                instance_mod.update_instance("updtest")

        assert "Rolled back" not in str(excinfo.value)
        assert get_instance("updtest")["version"] == "0.23.0"


# ---------------------------------------------------------------------------
# instance.create_instance — auto-backup wiring
# ---------------------------------------------------------------------------


class TestCreateInstanceAutoBackup:
    """Tests for the create → configure_auto_backup wiring."""

    def test_create_configures_auto_backup_with_defaults(
        self, isolated_env, mock_create_deps
    ):
        """With auto_backups_enabled and superadmin creds, PocketBase's
        schedule is configured from defaults.* and state records it."""
        from pocketmanager.core import instance as instance_mod

        with (
            patch(
                "pocketmanager.core.backup.authenticate", return_value="tok123"
            ) as auth_mock,
            patch(
                "pocketmanager.core.backup.configure_auto_backup",
                return_value=True,
            ) as configure_mock,
        ):
            record = instance_mod.create_instance(
                "abtest", port=9122, pangolin=False
            )

        auth_mock.assert_called_once_with(
            "http://127.0.0.1:9122", "admin@pocketbase.local", "secretpw"
        )
        configure_mock.assert_called_once_with(
            "http://127.0.0.1:9122",
            "0 3 * * *",  # defaults.auto_backups_cron from isolated_env
            7,            # defaults.auto_backups_max_keep from isolated_env
            auth_token="tok123",
        )

        assert record["auto_backup"] is True
        assert record["backup_cron"] == "0 3 * * *"
        assert record["backup_max_keep"] == 7
        assert not record.get("backup_warning")

    def test_create_backup_warning_when_configure_fails(
        self, isolated_env, mock_create_deps
    ):
        """A failing configure_auto_backup leaves auto_backup=False in state
        and surfaces a backup_warning."""
        from pocketmanager.core import instance as instance_mod

        with (
            patch("pocketmanager.core.backup.authenticate", return_value="tok123"),
            patch(
                "pocketmanager.core.backup.configure_auto_backup",
                return_value=False,
            ),
        ):
            record = instance_mod.create_instance(
                "abtest2", port=9123, pangolin=False
            )

        assert record["auto_backup"] is False
        assert record["backup_cron"] == ""
        assert record["backup_max_keep"] == 0
        assert record["backup_warning"]
        assert "local-backup-schedule" in record["backup_warning"]


# ---------------------------------------------------------------------------
# instance.create_instance — partial-failure cleanup
# ---------------------------------------------------------------------------


class TestCreateInstanceCleanup:
    """Tests for the undo path when provisioning fails partway."""

    def test_create_failure_removes_dir_and_unit_file(
        self, isolated_env, mock_create_deps
    ):
        """A create_service failure propagates, removes the instance dir, and
        invokes sudo rm for the unit file."""
        from pocketmanager.core import instance as instance_mod

        instance_dir = isolated_env["base_dir"] / "pocketbase-cleanfail"

        with patch(
            "pocketmanager.core.instance.create_service",
            side_effect=RuntimeError("systemctl exploded"),
        ):
            with pytest.raises(RuntimeError, match="systemctl exploded"):
                instance_mod.create_instance(
                    "cleanfail", port=9124, pangolin=False
                )

        assert not instance_dir.exists()

        commands = [
            c.args[0]
            for c in mock_create_deps["subprocess_run"].call_args_list
            if c.args
        ]
        assert [
            "sudo", "rm", "-f",
            "/etc/systemd/system/pocketbase-cleanfail.service",
        ] in commands


# ---------------------------------------------------------------------------
# cron converter
# ---------------------------------------------------------------------------


class TestCronToOnCalendar:
    """Tests for the 5-field cron → OnCalendar converter."""

    @pytest.mark.parametrize(
        "expr,expected",
        [
            ("0 3 * * 0-7", "*-*-* 03:00:00"),   # full week (both 0 and 7 = Sun)
            ("0 3 * * 1-7", "*-*-* 03:00:00"),   # Mon..Sun covers the week
            ("0 3 * * 0-6", "*-*-* 03:00:00"),   # Sun..Sat covers the week
            ("0 3 * * 1-5", "Mon..Fri *-*-* 03:00:00"),
            ("0 3 * * 0", "Sun *-*-* 03:00:00"),
        ],
        ids=["0-7", "1-7", "0-6", "monday-friday", "sunday-only"],
    )
    def test_conversion(self, expr, expected):
        from pocketmanager.core.cron import _cron_to_oncalendar

        assert _cron_to_oncalendar(expr) == expected

    def test_inverted_range_raises(self):
        """An inverted day-of-week range must fail loudly, not guess."""
        from pocketmanager.core.cron import _cron_to_oncalendar

        with pytest.raises(ValueError):
            _cron_to_oncalendar("0 3 * * 5-1")


# ---------------------------------------------------------------------------
# cron.set_sftp_cron / remove_sftp_cron
# ---------------------------------------------------------------------------


class TestSftpCronUnits:
    """Tests for the systemd timer/service pair written by set_sftp_cron."""

    def test_set_sftp_cron_writes_units(self):
        """set_sftp_cron() writes service+timer units with the converted
        schedule and the non-root user, then cleans the legacy crontab."""
        from pocketmanager.core import cron as cron_mod

        with (
            patch("pocketmanager.core.cron._write_unit_file") as write_mock,
            patch("pocketmanager.core.cron._systemctl") as systemctl_mock,
            patch(
                "pocketmanager.core.cron._get_service_user",
                return_value="ubuntu",
            ),
            patch(
                "pocketmanager.core.cron._find_pm_binary",
                return_value="/usr/local/bin/pm",
            ),
            patch(
                "pocketmanager.core.cron._cleanup_legacy_crontab"
            ) as cleanup_mock,
        ):
            assert cron_mod.set_sftp_cron("0 3 * * *") is True

        written = {c.args[0]: c.args[1] for c in write_mock.call_args_list}
        assert cron_mod._SERVICE_PATH in written
        assert cron_mod._TIMER_PATH in written
        assert "OnCalendar=*-*-* 03:00:00" in written[cron_mod._TIMER_PATH]
        assert "User=ubuntu" in written[cron_mod._SERVICE_PATH]
        assert "backup-all --push" in written[cron_mod._SERVICE_PATH]

        assert systemctl_mock.call_args_list == [
            call("daemon-reload"),
            call("enable", "pm-sftp-backup.timer"),
            call("restart", "pm-sftp-backup.timer"),
        ]
        cleanup_mock.assert_called_once()

    def test_remove_sftp_cron_removes_units(self):
        """remove_sftp_cron() removes both unit files and cleans the legacy
        crontab so old backups stop running."""
        from pocketmanager.core import cron as cron_mod

        with (
            patch("pocketmanager.core.cron._systemctl"),
            patch("pocketmanager.core.cron._remove_unit_file") as remove_mock,
            patch(
                "pocketmanager.core.cron._cleanup_legacy_crontab"
            ) as cleanup_mock,
        ):
            assert cron_mod.remove_sftp_cron() is True

        removed = {c.args[0] for c in remove_mock.call_args_list}
        assert removed == {cron_mod._TIMER_PATH, cron_mod._SERVICE_PATH}
        cleanup_mock.assert_called_once()


# ---------------------------------------------------------------------------
# sftp upload / prune with a fake paramiko client
# ---------------------------------------------------------------------------


class _FakeAttr:
    """Stand-in for an SFTPAttributes entry returned by listdir_attr()."""

    def __init__(self, filename: str, mtime: float, size: int = 1024):
        self.filename = filename
        self.st_mtime = mtime
        self.st_size = size


class _FakeSftpPrune:
    """SFTPClient double serving a fixed listing and recording deletions."""

    def __init__(self, files_by_dir: dict[str, list[_FakeAttr]]):
        self.files_by_dir = files_by_dir
        self.removed: list[str] = []

    def listdir_attr(self, path):
        return list(self.files_by_dir.get(path, []))

    def remove(self, path):
        self.removed.append(path)

    def stat(self, path):
        raise FileNotFoundError(path)

    def mkdir(self, path):
        pass

    def close(self):
        pass


class _FakeSftpUpload:
    """SFTPClient double verifying uploaded file sizes via stat()."""

    def __init__(self):
        self.sizes: dict[str, int] = {}
        self.uploaded: list[tuple[str, str]] = []

    def stat(self, path):
        if path not in self.sizes:
            raise FileNotFoundError(path)
        attr = MagicMock()
        attr.st_size = self.sizes[path]
        return attr

    def mkdir(self, path):
        pass

    def put(self, local_path, remote_path):
        self.uploaded.append((local_path, remote_path))
        self.sizes[remote_path] = os.path.getsize(local_path)

    def close(self):
        pass


def _fake_ssh_client(fake_sftp):
    """Return a MagicMock SSHClient wired to *fake_sftp* (no TOFU pinning)."""
    client = MagicMock()
    client.get_transport.return_value = None  # skip host-key pinning block
    client.open_sftp.return_value = fake_sftp
    return client


_SFTP_CONFIG = {
    "enabled": True,
    "host": "sftp.invalid",  # never resolves — no real connection attempt
    "port": 22,
    "username": "u123",
    "password": "s3cret",
    "remote_path": "backups",
    "max_remote_backups": 2,
}


class TestSftpWithFakeParamiko:
    """SFTP upload/prune against a patched paramiko.SSHClient."""

    def test_cleanup_remote_backups_prunes_oldest(self):
        """cleanup_remote_backups() keeps the newest max_keep files and
        deletes the oldest ones."""
        from pocketmanager.core.sftp import cleanup_remote_backups

        files = [
            _FakeAttr("pb_backup_20260101.zip", mtime=1000),
            _FakeAttr("pb_backup_20260102.zip", mtime=2000),
            _FakeAttr("pb_backup_20260103.zip", mtime=3000),
            _FakeAttr("pb_backup_20260104.zip", mtime=4000),
        ]
        fake_sftp = _FakeSftpPrune({"backups/myapp": files})

        with patch(
            "pocketmanager.core.sftp.paramiko.SSHClient",
            return_value=_fake_ssh_client(fake_sftp),
        ):
            deleted_count, deleted = cleanup_remote_backups(
                "myapp", _SFTP_CONFIG, max_keep=2
            )

        assert deleted_count == 2
        assert deleted == [
            "pb_backup_20260102.zip",
            "pb_backup_20260101.zip",
        ]
        # Full remote paths of the two oldest were removed; the newest kept.
        assert "backups/myapp/pb_backup_20260101.zip" in fake_sftp.removed
        assert "backups/myapp/pb_backup_20260102.zip" in fake_sftp.removed
        assert "backups/myapp/pb_backup_20260103.zip" not in fake_sftp.removed
        assert "backups/myapp/pb_backup_20260104.zip" not in fake_sftp.removed

    def test_cleanup_remote_backups_noop_at_limit(self):
        """With fewer files than max_keep nothing is deleted."""
        from pocketmanager.core.sftp import cleanup_remote_backups

        files = [_FakeAttr("pb_backup_a.zip", mtime=1000)]
        fake_sftp = _FakeSftpPrune({"backups/myapp": files})

        with patch(
            "pocketmanager.core.sftp.paramiko.SSHClient",
            return_value=_fake_ssh_client(fake_sftp),
        ):
            deleted_count, deleted = cleanup_remote_backups(
                "myapp", _SFTP_CONFIG, max_keep=2
            )

        assert deleted_count == 0
        assert deleted == []
        assert fake_sftp.removed == []

    def test_upload_backup_verifies_size(self, tmp_path):
        """upload_backup() puts the file under <remote_path>/<instance>/
        and succeeds when the remote size matches."""
        from pocketmanager.core.sftp import upload_backup

        local = tmp_path / "pb_backup_20260105.zip"
        local.write_bytes(b"z" * 4096)
        fake_sftp = _FakeSftpUpload()

        with patch(
            "pocketmanager.core.sftp.paramiko.SSHClient",
            return_value=_fake_ssh_client(fake_sftp),
        ):
            ok, result = upload_backup(
                str(local), "myapp", _SFTP_CONFIG
            )

        assert ok is True
        assert result == "backups/myapp/pb_backup_20260105.zip"
        assert fake_sftp.uploaded == [
            (str(local), "backups/myapp/pb_backup_20260105.zip")
        ]


# ---------------------------------------------------------------------------
# backup HTTP error paths
# ---------------------------------------------------------------------------


def _http_error_response(status: int) -> MagicMock:
    """Build a fake requests response whose raise_for_status() raises
    an HTTPError carrying the given status."""
    resp = MagicMock()
    resp.status_code = status
    err = requests.HTTPError(f"{status} Server Error")
    err.response = resp
    resp.raise_for_status.side_effect = err
    return resp


class TestBackupHttpErrors:
    """Tests for create_backup_ex / restore_backup_ex / is_instance_reachable."""

    def test_create_backup_ex_http_500(self):
        """A 500 from PocketBase maps to (False, 'HTTP 500')."""
        from pocketmanager.core import backup as backup_mod

        with patch(
            "pocketmanager.core.backup.requests.post",
            return_value=_http_error_response(500),
        ) as post_mock:
            ok, reason = backup_mod.create_backup_ex("http://localhost:8090")

        assert ok is False
        assert reason == "HTTP 500"
        post_mock.assert_called_once_with(
            "http://localhost:8090/api/backups",
            json=None,
            headers={},
            timeout=30,
        )

    def test_create_backup_ex_connection_error(self):
        """A connection failure maps to (False, 'connection error (...)')."""
        from pocketmanager.core import backup as backup_mod

        with patch(
            "pocketmanager.core.backup.requests.post",
            side_effect=requests.ConnectionError("connection refused"),
        ):
            ok, reason = backup_mod.create_backup_ex("http://localhost:8090")

        assert ok is False
        assert "connection error" in reason

    def test_restore_backup_ex_404(self):
        """A 404 on restore reports the backup key as not found."""
        from pocketmanager.core import backup as backup_mod

        with patch(
            "pocketmanager.core.backup.requests.post",
            return_value=_http_error_response(404),
        ):
            ok, reason = backup_mod.restore_backup_ex(
                "http://localhost:8090", "missing_key.zip"
            )

        assert ok is False
        assert "not found" in reason
        assert "missing_key.zip" in reason

    def test_is_instance_reachable_connection_error(self):
        """A connection error means unreachable."""
        from pocketmanager.core import backup as backup_mod

        with patch(
            "pocketmanager.core.backup.requests.get",
            side_effect=requests.ConnectionError("refused"),
        ):
            assert backup_mod.is_instance_reachable("http://localhost:8090") is False

    def test_is_instance_reachable_200(self):
        """A 200 from the health endpoint means reachable."""
        from pocketmanager.core import backup as backup_mod

        resp = MagicMock()
        resp.status_code = 200
        with patch(
            "pocketmanager.core.backup.requests.get", return_value=resp
        ) as get_mock:
            assert backup_mod.is_instance_reachable("http://localhost:8090") is True
        get_mock.assert_called_once_with(
            "http://localhost:8090/api/health", timeout=3.0
        )


class TestSelfUpdatePipFlags:
    """perform_update must pass --break-system-packages only on pip 23+."""

    @staticmethod
    def _perform(captured, returncode=0):
        from subprocess import CompletedProcess

        from pocketmanager.core import selfupdate as su

        def fake_run(cmd, **kwargs):
            if "--help" in cmd:
                return CompletedProcess(cmd, 0, stdout="--break-system-packages")
            captured.extend(cmd)
            return CompletedProcess(cmd, returncode)

        # _write_local_commit MUST be patched — it writes into the real
        # package's commit.txt (this test once clobbered the actual marker).
        with patch.object(su, "subprocess") as fake_subprocess, \
                patch.object(su, "_fetch_remote_commit", return_value="abc123"), \
                patch.object(su, "_write_local_commit") as write_marker:
            fake_subprocess.run = fake_run
            fake_subprocess.TimeoutExpired = Exception
            result = su.perform_update()
            write_marker.assert_called_once_with("abc123")
            return result

    def test_flag_included_when_supported(self):
        """pip 23+ gets the flag in the install command."""
        captured: list = []
        assert self._perform(captured) is True
        assert "--break-system-packages" in captured
        assert "--force-reinstall" in captured

    def test_flag_omitted_when_unsupported(self, monkeypatch):
        """Old pip (Ubuntu 22.04's 22.0.2) must not receive the flag."""
        from pocketmanager.core import selfupdate as su

        monkeypatch.setattr(su, "_pip_break_system_packages_supported", lambda: False)
        captured: list = []
        assert self._perform(captured) is True
        assert "--break-system-packages" not in captured
