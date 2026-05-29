"""Systemd service management for PocketBase instances.

Creates, removes, and controls systemd service files so PocketBase instances
run as managed system services under a dedicated ``pocketbase`` system user.

All operations that touch systemd or system files require sudo.  The helper
functions in this module build and run the appropriate ``subprocess`` commands
— callers must ensure they have the necessary sudo access.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Env sanitization
# ---------------------------------------------------------------------------

_FORBIDDEN_ENV_CHARS = {'"', "'", "\n", "\r", "\\"}


def _sanitize_env(env: dict[str, str]) -> dict[str, str]:
    """Validate environment variable keys and values for systemd unit files.

    Rejects characters that could break out of quoting and inject arbitrary
    systemd directives.
    """
    for key, value in env.items():
        if not key or "=" in key:
            raise ValueError(f"Invalid environment variable key: {key!r}")
        for ch in _FORBIDDEN_ENV_CHARS:
            if ch in key:
                raise ValueError(f"Forbidden character in env key {key!r}")
            if ch in value:
                raise ValueError(
                    f"Forbidden character in env value for {key!r}: {value!r}"
                )
    return env


# ---------------------------------------------------------------------------
# Path / name helpers
# ---------------------------------------------------------------------------


def get_service_path(name: str) -> Path:
    """Return the absolute path to the systemd service file for *name*.

    Example::

        get_service_path("myproj")  # /etc/systemd/system/pocketbase-myproj.service
    """
    return Path(f"/etc/systemd/system/pocketbase-{name}.service")


def get_service_name(name: str) -> str:
    """Return the systemd unit name for *name*.

    Example::

        get_service_name("myproj")  # pocketbase-myproj
    """
    return f"pocketbase-{name}"


# ---------------------------------------------------------------------------
# Service file generation
# ---------------------------------------------------------------------------


def generate_service_content(
    name: str,
    port: int,
    working_dir: str,
    env: dict[str, str] | None = None,
) -> str:
    """Generate the full content of a systemd service unit file.

    Parameters
    ----------
    name:
        Instance name (used in Description, SyslogIdentifier, etc.).
    port:
        HTTP port the PocketBase process will bind to.
    working_dir:
        Full path to the instance directory (e.g.
        ``/home/ubuntu/pocketbases/pocketbase-myproj``).
    env:
        Optional environment variables.  Each ``KEY=VALUE`` pair becomes an
        ``Environment="KEY=VALUE"`` line in the ``[Service]`` section.

    Returns
    -------
    str
        The complete service file text.
    """
    env = env or {}
    _sanitize_env(env) if env else None
    env_lines = ""
    if env:
        env_lines = "\n".join(f'Environment="{k}={v}"' for k, v in env.items())

    # Health-check lines — only include when curl is available on the host.
    health_check_lines = ""
    if shutil.which("curl"):
        health_check_lines = (
            "\n# Health check\n"
            f"ExecStartPost=/bin/sleep 2\n"
            f"ExecStartPost=/usr/bin/curl -sf http://localhost:{port}/api/health\n"
        )

    content = f"""\
[Unit]
Description=PocketBase - {name}
Documentation=https://pocketbase.io/docs/
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pocketbase
Group=pocketbase
LimitNOFILE=4096
Restart=on-failure
RestartSec=5s
StartLimitIntervalSec=300
StartLimitBurst=5

WorkingDirectory={working_dir}
ExecStart={working_dir}/pocketbase serve --http=0.0.0.0:{port}
{env_lines}
{health_check_lines}
# Security
ProtectSystem=strict
ReadWritePaths={working_dir}/pb_data
ProtectHome=read-only
PrivateTmp=yes
NoNewPrivileges=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictNamespaces=true
MemoryDenyWriteExecute=true

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=pocketbase-{name}

[Install]
WantedBy=multi-user.target
"""
    # Clean up any blank lines that may result from an empty env block.
    return "\n".join(line for line in content.splitlines() if line.strip()) + "\n"


# ---------------------------------------------------------------------------
# Service lifecycle
# ---------------------------------------------------------------------------


def create_service(
    name: str,
    port: int,
    working_dir: str,
    env: dict[str, str] | None = None,
) -> Path:
    """Write a systemd service file and reload the daemon.

    Uses ``sudo tee`` to write the file (requires sudo).

    Parameters
    ----------
    name:
        Instance name.
    port:
        HTTP port for the instance.
    working_dir:
        Full path to the instance directory.
    env:
        Optional environment variables.

    Returns
    -------
    Path
        The path where the service file was written.

    Raises
    ------
    subprocess.CalledProcessError
        If the ``tee`` or ``daemon-reload`` command fails.
    """
    service_path = get_service_path(name)
    content = generate_service_content(name, port, working_dir, env)

    # Write the service file via sudo tee
    subprocess.run(
        ["sudo", "tee", str(service_path)],
        input=content.encode("utf-8"),
        check=True,
    )

    # Reload systemd so it picks up the new unit
    subprocess.run(
        ["sudo", "systemctl", "daemon-reload"],
        check=True,
    )

    return service_path


def remove_service(name: str) -> bool:
    """Stop, disable, and remove the systemd service for *name*.

    Stop errors are silently ignored (the service may not be running).
    Returns ``True`` if the removal succeeded end-to-end.

    Parameters
    ----------
    name:
        Instance name.
    """
    service_name = get_service_name(name)
    service_path = get_service_path(name)

    # Stop — ignore failures (service may already be stopped)
    subprocess.run(
        ["sudo", "systemctl", "stop", service_name],
        capture_output=True,
    )

    # Disable
    try:
        subprocess.run(
            ["sudo", "systemctl", "disable", service_name],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        pass

    # Remove the service file
    try:
        subprocess.run(
            ["sudo", "rm", "-f", str(service_path)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return False

    # Reload daemon
    try:
        subprocess.run(
            ["sudo", "systemctl", "daemon-reload"],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return False

    return True


# ---------------------------------------------------------------------------
# Service control
# ---------------------------------------------------------------------------


def start_service(name: str) -> bool:
    """Start the systemd service for *name*.

    Returns ``True`` if the service started successfully (exit code 0).
    """
    service_name = get_service_name(name)
    try:
        subprocess.run(
            ["sudo", "systemctl", "start", service_name],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def stop_service(name: str) -> bool:
    """Stop the systemd service for *name*.

    Returns ``True`` if the service stopped successfully (exit code 0).
    """
    service_name = get_service_name(name)
    try:
        subprocess.run(
            ["sudo", "systemctl", "stop", service_name],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def restart_service(name: str) -> bool:
    """Restart the systemd service for *name*.

    Returns ``True`` if the service restarted successfully (exit code 0).
    """
    service_name = get_service_name(name)
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", service_name],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def is_active(name: str) -> bool:
    """Return ``True`` if the systemd service for *name* is in the *active* state."""
    service_name = get_service_name(name)
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def get_status(name: str) -> dict:
    """Return a status dict for the systemd service of *name*.

    Keys:

    * ``active`` (bool) — whether the service is active.
    * ``status_text`` (str) — raw output of ``systemctl is-active``.
    * ``uptime_seconds`` (float | None) — seconds since the service entered
      the active state, or ``None`` if unavailable.
    * ``pid`` (int | None) — main process ID, or ``None`` if not running.
    """
    service_name = get_service_name(name)

    # Determine active / status_text
    status_text = "unknown"
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            check=False,
        )
        status_text = result.stdout.strip()
    except Exception:
        pass

    active = status_text == "active"

    # Gather detailed properties
    uptime_seconds: float | None = None
    pid: int | None = None

    try:
        result = subprocess.run(
            [
                "systemctl", "show", service_name,
                "--property=ActiveState,ActiveEnterTimestampSec,MainPID",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        props: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                props[key.strip()] = value.strip()

        # Parse uptime
        raw_ts = props.get("ActiveEnterTimestampSec", "")
        if raw_ts:
            try:
                ts = float(raw_ts)
                import time
                uptime_seconds = time.time() - ts
            except (ValueError, OSError):
                pass

        # Parse PID
        raw_pid = props.get("MainPID", "0")
        try:
            pid_val = int(raw_pid)
            pid = pid_val if pid_val > 0 else None
        except ValueError:
            pass

    except Exception:
        pass

    return {
        "active": active,
        "status_text": status_text,
        "uptime_seconds": uptime_seconds,
        "pid": pid,
    }


def get_journal_logs(
    name: str,
    lines: int = 100,
    follow: bool = False,
) -> str:
    """Fetch journal logs for the systemd service of *name*.

    Parameters
    ----------
    name:
        Instance name.
    lines:
        Number of log lines to retrieve (passed to ``-n``).
    follow:
        If ``True``, append ``-f`` to follow the log output.  **Note:** this
        will block indefinitely until interrupted.

    Returns
    -------
    str
        The journal output.
    """
    service_name = get_service_name(name)
    cmd: list[str] = [
        "journalctl",
        "-u", service_name,
        "-n", str(lines),
        "--no-pager",
    ]
    if follow:
        cmd.append("-f")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# System user and permissions
# ---------------------------------------------------------------------------


def ensure_pocketbase_user() -> bool:
    """Ensure the ``pocketbase`` system user exists.

    Checks with ``id pocketbase``.  If the user is absent, creates it via
    ``sudo useradd --system --shell /bin/false --home /home/ubuntu/pocketbases pocketbase``.

    Returns ``True`` if the user exists or was successfully created.
    """
    # Check if user already exists
    check = subprocess.run(
        ["id", "pocketbase"],
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode == 0:
        return True

    # Create the system user
    try:
        from pocketmanager.core.config import get as cfg_get

        base_dir = cfg_get("base_dir", "/home/ubuntu/pocketbases")
        subprocess.run(
            [
                "sudo", "useradd",
                "--system",
                "--shell", "/bin/false",
                "--home", base_dir,
                "pocketbase",
            ],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def set_permissions(instance_dir: str) -> None:
    """Set ownership of *instance_dir* to ``pocketbase:pocketbase``.

    Uses ``sudo chown -R``.
    """
    subprocess.run(
        ["sudo", "chown", "-R", "pocketbase:pocketbase", instance_dir],
        check=True,
        capture_output=True,
    )


def set_capabilities(binary_path: str) -> None:
    """Grant network bind capability to the PocketBase binary.

    Sets ``cap_net_bind_service=+ep`` so the binary can bind to privileged
    ports (< 1024) without running as root.
    """
    subprocess.run(
        ["sudo", "setcap", "cap_net_bind_service=+ep", binary_path],
        check=True,
        capture_output=True,
    )
