"""High-level instance orchestrator for PocketManager.

This module ties together all sibling core modules (config, state, ports,
pocketbase, systemd) and exposes the public functions that the CLI layer calls.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from pocketmanager.core.config import get
from pocketmanager.core.pocketbase import detect_instance_version, ensure_binary, get_latest_version
from pocketmanager.core.ports import find_available_port, is_port_free
from pocketmanager.core.state import (
    add_instance as state_add_instance,
    get_all_instances,
    get_instance as state_get_instance,
    remove_instance as state_remove_instance,
    update_instance as state_update_instance,
)
from pocketmanager.core.systemd import (
    create_service,
    ensure_pocketbase_user,
    get_status,
    is_active,
    remove_service,
    restart_service,
    set_capabilities,
    set_permissions,
    start_service,
    stop_service,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_name(name: str) -> None:
    """Validate instance name.

    Rules:
    * Non-empty string.
    * Lowercase alphanumeric characters and hyphens only.
    * Must not start or end with a hyphen.
    * Maximum 64 characters.
    * Must not already exist in state.

    Raises:
        ValueError: If any rule is violated.
    """
    if not name or not isinstance(name, str):
        raise ValueError("Instance name must be a non-empty string.")

    if len(name) > 64:
        raise ValueError("Instance name must be 64 characters or fewer.")

    if not re.match(r"^[a-z0-9-]+$", name):
        raise ValueError(
            "Instance name must contain only lowercase letters, digits, and hyphens."
        )

    if name.startswith("-") or name.endswith("-"):
        raise ValueError("Instance name must not start or end with a hyphen.")

    existing = state_get_instance(name)
    if existing is not None:
        raise ValueError(f"An instance named '{name}' already exists.")


def _get_instance_dir(name: str) -> Path:
    """Return the instance directory path.

    Uses ``base_dir`` from config: ``<base_dir>/pocketbase-<name>``.
    """
    base_dir = Path(get("base_dir", "/home/ubuntu/pocketbases"))
    return base_dir / f"pocketbase-{name}"


def _health_check(port: int) -> bool:
    """Return ``True`` if the PocketBase health endpoint responds with 200."""
    try:
        req = urllib.request.Request(f"http://localhost:{port}/api/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _get_disk_usage(path: Path) -> str:
    """Return human-readable disk usage for *path* (e.g. ``'42M'``)."""
    try:
        result = subprocess.run(
            ["du", "-sh", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        # Output format: "42M\tpath"
        return result.stdout.split("\t")[0].strip()
    except Exception:
        return "unknown"


def _create_superadmin(binary_path: str) -> tuple[str, str] | None:
    """Attempt to create the first superadmin on a fresh PocketBase instance.

    Uses the PocketBase CLI ``superuser upsert`` command which writes directly
    to the database.  Must be called **before** the service is started to avoid
    SQLite database locking issues.

    Returns ``(email, password)`` on success, or ``None`` on failure.
    """
    import secrets
    import string

    email = "admin@pocketbase.local"
    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(24))

    # Derive pb_data directory from binary_path (binary lives inside instance dir)
    pb_data_dir = str(Path(binary_path).resolve().parent / "pb_data")

    try:
        result = subprocess.run(
            ["sudo", "-u", "pocketbase", binary_path, "superuser", "upsert", email, password, "--dir", pb_data_dir],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return email, password
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_instance(
    name: str,
    port: int | None = None,
    domain: str | None = None,
    env: dict[str, str] | None = None,
    version: str | None = None,
    pangolin: bool = True,
) -> dict:
    """Create a new PocketBase instance.

    Performs the full creation flow: validation → version resolution → port
    allocation → directory setup → binary deployment → systemd service creation
    → start → optional pangolin integration → state registration.

    Parameters
    ----------
    name:
        Instance name (validated by :func:`_validate_name`).
    port:
        HTTP port.  If ``None``, the next free port in the configured range is
        allocated automatically.
    domain:
        Full domain for the instance (e.g. ``"myapp.apps.example.com"``).
        Used both as the public URL and to derive the subdomain for Pangolin
        integration by stripping the configured ``pangolin.default_domain``.
    env:
        Extra environment variables passed to the service.
    version:
        PocketBase version string (e.g. ``"0.39.0"``).  Defaults to the latest
        release.
    pangolin:
        Whether to register the instance with pangolin (if configured).

    Returns
    -------
    dict
        The registered instance record.

    Raises
    ------
    ValueError
        On validation errors (name, port).
    RuntimeError
        On irrecoverable system errors during creation.
    """
    # 1. Validate name
    _validate_name(name)

    # 2. Resolve version
    if version is None:
        version = get_latest_version()

    # 3. Allocate / validate port
    if port is not None:
        if not is_port_free(port):
            raise ValueError(
                f"Port {port} is already in use. Choose a different port."
            )
    else:
        port = find_available_port()

    # 4. Ensure pocketbase system user
    ensure_pocketbase_user()

    # 5. Calculate paths
    instance_dir = _get_instance_dir(name)

    # 6. Create directory structure
    (instance_dir / "pb_data").mkdir(parents=True, exist_ok=True)
    (instance_dir / "pb_hooks").mkdir(parents=True, exist_ok=True)
    (instance_dir / "pb_migrations").mkdir(parents=True, exist_ok=True)

    # 7. Download / copy binary
    cached_binary = ensure_binary(version)
    binary_path = instance_dir / "pocketbase"
    shutil.copy2(str(cached_binary), str(binary_path))
    binary_path.chmod(0o755)

    # 8. Set permissions
    set_permissions(str(instance_dir))

    # 9. Set capabilities
    set_capabilities(str(binary_path))

    # 10. Create systemd service
    create_service(name, port, str(instance_dir), env=env)

    # 11. Auto-create superadmin (before starting service to avoid DB lock)
    admin_email: str | None = None
    admin_password: str | None = None
    admin_warning: str | None = None
    try:
        creds = _create_superadmin(str(binary_path))
        if creds:
            admin_email, admin_password = creds
        else:
            admin_warning = (
                f"Could not auto-create superadmin. "
                f"Visit the Admin UI at http://localhost:{port}/_/ "
                f"to create one manually, then run 'pm credentials {name}'."
            )
    except Exception:
        admin_warning = (
            f"Could not auto-create superadmin. "
            f"Visit the Admin UI at http://localhost:{port}/_/ "
            f"to create one manually, then run 'pm credentials {name}'."
        )

    # 12. Start service
    start_service(name)

    # 13. Pangolin integration (optional, non-blocking)
    pangolin_resource_id: str | None = None
    pangolin_warning: str | None = None
    if pangolin:
        from pocketmanager.core.pangolin import PangolinAPIError, PangolinConfigError

        try:
            from pocketmanager.core import pangolin as pangolin_mod  # noqa: F811

            # Derive the subdomain for Pangolin from the full domain by
            # stripping the configured default_domain.  For example, if
            # domain="myapp.apps.example.com" and
            # pangolin.default_domain="example.com", the subdomain sent to
            # Pangolin is "myapp.apps".
            pangolin_subdomain: str | None = None
            if domain:
                default_domain = get("pangolin.default_domain", "")
                if default_domain and domain.endswith(f".{default_domain}"):
                    pangolin_subdomain = domain[: -(len(default_domain) + 1)]
                else:
                    # No matching base domain — use the full domain as subdomain
                    pangolin_subdomain = domain

            site_id_raw = get("pangolin.site_id", "")
            site_id = int(site_id_raw) if site_id_raw else 0
            result = pangolin_mod.create_resource(
                name=name,
                subdomain=pangolin_subdomain,
                domain_id=get("pangolin.default_domain_id", ""),
                org_id=get("pangolin.org_id", ""),
                site_id=site_id,
                target_ip=get("pangolin.target_ip", "127.0.0.1"),
                target_port=port,
            )
            pangolin_resource_id = result.get("resourceId")
        except PangolinConfigError as exc:
            pangolin_warning = str(exc)
        except PangolinAPIError as exc:
            pangolin_warning = f"Pangolin resource creation failed: {exc}"
        except Exception as exc:
            pangolin_warning = f"Pangolin resource creation failed: {exc}"
            pangolin_resource_id = None

    # 14. Register in state
    instance_record: dict[str, Any] = {
        "name": name,
        "port": port,
        "version": version,
        "instance_dir": str(instance_dir),
        "domain": domain,
        "env": env or {},
        "pangolin_resource_id": pangolin_resource_id,
        "auto_backup": get("defaults.auto_backups_enabled", True),
        "superadmin_email": admin_email,
        "superadmin_password": admin_password,
    }
    state_add_instance(instance_record)

    # Return a copy with the auto-populated fields (slug, created_at)
    result = state_get_instance(name)  # type: ignore[assignment]
    if result is not None:
        result["pangolin_warning"] = pangolin_warning
        result["admin_warning"] = admin_warning
    return result  # type: ignore[return-value]


def remove_instance(
    name: str,
    keep_data: bool = False,
    remove_pangolin: bool = True,
) -> dict:
    """Remove a PocketBase instance.

    Parameters
    ----------
    name:
        Instance name.
    keep_data:
        If ``True``, the instance directory is left on disk.
    remove_pangolin:
        If ``True`` and the instance has a pangolin resource, attempt to delete
        it.

    Returns
    -------
    dict
        The removed instance record.

    Raises
    ------
    ValueError
        If the instance is not found in state.
    """
    # 1. Look up instance
    instance = state_get_instance(name)
    if instance is None:
        raise ValueError(f"Instance '{name}' not found.")

    # 2. Stop service (ignore errors)
    stop_service(name)

    # 3. Remove systemd service
    remove_service(name)

    # 4. Pangolin cleanup (optional, non-blocking)
    if remove_pangolin and instance.get("pangolin_resource_id"):
        try:
            from pocketmanager.core import pangolin as pangolin_mod

            pangolin_mod.delete_resource(instance["pangolin_resource_id"])
        except Exception:
            pass

    # 5. Delete instance directory
    instance_dir = instance.get("instance_dir", "")
    if not keep_data and instance_dir:
        # Validate that instance_dir is under base_dir to prevent path traversal
        base_dir = get("base_dir", "/home/ubuntu/pocketbases")
        resolved_dir = Path(instance_dir).resolve()
        resolved_base = Path(base_dir).resolve()
        if not str(resolved_dir).startswith(str(resolved_base) + "/") and resolved_dir != resolved_base:
            raise ValueError(
                f"Refusing to delete '{instance_dir}': path is outside base_dir '{base_dir}'. "
                "Possible state file tampering."
            )
        subprocess.run(
            ["sudo", "rm", "-rf", str(resolved_dir)],
            capture_output=True,
            check=False,
        )

    # 6. Remove from state
    removed = state_remove_instance(name)
    if removed is None:
        # Shouldn't happen since we already found it, but be safe
        raise ValueError(f"Failed to remove instance '{name}' from state.")

    return removed


def list_instances() -> list[dict]:
    """Return all registered instances enriched with current status.

    Each dict includes the original state fields plus an ``active`` key.
    """
    instances = get_all_instances()
    result: list[dict] = []
    for inst in instances:
        enriched = dict(inst)
        enriched["active"] = is_active(inst.get("name", ""))
        result.append(enriched)
    return result


def get_instance_info(name: str) -> dict:
    """Get detailed information for a single instance.

    Returns a dict with all state fields plus runtime information:

    * ``active`` — whether the systemd service is running.
    * ``disk_usage`` — human-readable size of ``pb_data``.
    * ``health`` — ``True`` if the health endpoint responds.
    * ``uptime_seconds`` — seconds since the service entered active state.
    * ``backup_count`` — number of backups (0 if the backup module is
      unavailable).
    """
    instance = state_get_instance(name)
    if instance is None:
        raise ValueError(f"Instance '{name}' not found.")

    info: dict[str, Any] = dict(instance)

    # Systemd status
    status = get_status(name)
    info["active"] = status["active"]
    info["uptime_seconds"] = status["uptime_seconds"]

    # Disk usage
    instance_dir = Path(instance.get("instance_dir", ""))
    pb_data_dir = instance_dir / "pb_data"
    info["disk_usage"] = _get_disk_usage(pb_data_dir) if pb_data_dir.is_dir() else "unknown"

    # Health check
    port = instance.get("port")
    info["health"] = _health_check(port) if port else False

    # Backup count (lazy import — backup module may not exist)
    try:
        from pocketmanager.core import backup as backup_mod

        info["backup_count"] = backup_mod.get_backup_count(name)
    except Exception:
        info["backup_count"] = 0

    # Pangolin auth status
    resource_id = instance.get("pangolin_resource_id")
    if resource_id:
        try:
            from pocketmanager.core import pangolin as pangolin_mod

            info["pangolin_auth"] = pangolin_mod.get_resource_auth_info(resource_id)
        except Exception:
            info["pangolin_auth"] = None
    else:
        info["pangolin_auth"] = None

    return info


def start_instance(name: str) -> bool:
    """Start the systemd service for *name*.

    Raises:
        ValueError: If the instance is not found.
    """
    instance = state_get_instance(name)
    if instance is None:
        raise ValueError(f"Instance '{name}' not found.")
    return start_service(name)


def stop_instance(name: str) -> None:
    """Stop the systemd service for *name*.

    Raises:
        ValueError: If the instance is not found.
        RuntimeError: If the service fails to stop.
    """
    instance = state_get_instance(name)
    if instance is None:
        raise ValueError(f"Instance '{name}' not found.")
    stop_service(name)


def restart_instance(name: str) -> bool:
    """Restart the systemd service for *name*.

    Raises:
        ValueError: If the instance is not found.
    """
    instance = state_get_instance(name)
    if instance is None:
        raise ValueError(f"Instance '{name}' not found.")
    return restart_service(name)


def update_instance(name: str, version: str | None = None) -> dict:
    """Update the PocketBase binary for an instance.

    Parameters
    ----------
    name:
        Instance name.
    version:
        Target PocketBase version.  ``None`` means latest release.

    Returns
    -------
    dict
        The updated instance record.

    Raises
    ------
    ValueError
        If the instance is not found.
    RuntimeError
        On binary download or deployment failures.
    """
    instance = state_get_instance(name)
    if instance is None:
        raise ValueError(f"Instance '{name}' not found.")

    # 1. Stop service
    stop_service(name)

    # 2. Resolve version
    if version is None:
        version = get_latest_version()

    # 3. Download / cache new binary
    cached_binary = ensure_binary(version)

    instance_dir = Path(instance.get("instance_dir", ""))
    binary_path = instance_dir / "pocketbase"

    # 4. Backup old binary
    if binary_path.is_file():
        backup_path = instance_dir / "pocketbase.bak"
        subprocess.run(
            ["sudo", "cp", "-p", str(binary_path), str(backup_path)],
            check=True,
        )

    # 5. Replace binary
    subprocess.run(
        ["sudo", "cp", "-p", str(cached_binary), str(binary_path)],
        check=True,
    )
    subprocess.run(
        ["sudo", "chmod", "755", str(binary_path)],
        check=True,
    )

    # 6. Set permissions and capabilities
    set_permissions(str(instance_dir))
    set_capabilities(str(binary_path))

    # 7. Start service
    if not start_service(name):
        raise RuntimeError(
            f"Failed to start instance '{name}' after update. "
            f"Check service logs: journalctl -u pocketbase-{name}.service -n 50"
        )

    # 8. Update state
    state_update_instance(name, {"version": version})

    return state_get_instance(name)  # type: ignore[return-value]


def migrate_existing() -> list[dict]:
    """Scan for manually-created PocketBase instances and import them.

    Walks the configured ``base_dir`` looking for subdirectories matching the
    ``pocketbase-*`` pattern.  For each match that has a corresponding systemd
    service, an attempt is made to detect the version and port, then the
    instance is registered in state.

    Returns a list of dicts for each newly-migrated instance.
    """
    base_dir = Path(get("base_dir", "/home/ubuntu/pocketbases"))
    if not base_dir.is_dir():
        return []

    migrated: list[dict] = []
    existing_names = {inst.get("name", "").lower() for inst in get_all_instances()}

    for entry in sorted(base_dir.iterdir()):
        if not entry.is_dir():
            continue
        if not entry.name.startswith("pocketbase-"):
            continue

        name = entry.name[len("pocketbase-"):]
        if name.lower() in existing_names:
            continue

        # Check if systemd service exists (standard or vendor location)
        service_path = Path(f"/etc/systemd/system/pocketbase-{name}.service")
        if not service_path.is_file():
            service_path = Path(f"/usr/lib/systemd/system/pocketbase-{name}.service")
        if not service_path.is_file():
            continue

        # Parse service file for port and working directory
        port: int | None = None
        working_dir: str = str(entry)
        env_vars: dict[str, str] = {}

        try:
            content = service_path.read_text(encoding="utf-8")
            for line in content.splitlines():
                stripped = line.strip()
                # Normalize spaces around '=' for systemd directives
                # (some service files use "ExecStart = ..." instead of "ExecStart=...")
                directive, _, value = stripped.partition("=")
                directive = directive.strip()
                value = value.strip()

                if directive == "ExecStart":
                    # Parse --http=0.0.0.0:PORT
                    import re as _re

                    port_match = _re.search(r"--http=.*?:(\d+)", value)
                    if port_match:
                        port = int(port_match.group(1))
                elif directive == "Environment":
                    # Remove surrounding quotes
                    env_str = value.strip().strip('"')
                    key, _, val = env_str.partition("=")
                    env_vars[key] = val
                elif directive == "WorkingDirectory":
                    working_dir = value
        except Exception:
            pass

        # Allocate port if not found in service file
        if port is None:
            try:
                port = find_available_port()
            except RuntimeError:
                port = 8090  # fallback

        # Detect version
        detected_version = detect_instance_version(entry) or "unknown"

        # Regenerate service file with hardened template
        try:
            create_service(name, port, working_dir, env=env_vars or None)
        except Exception:
            pass

        # Remove stale vendor service file if it exists (e.g. /usr/lib/systemd/system/...)
        # Our managed services live in /etc/systemd/system/ which takes precedence.
        try:
            vendor_path = Path(f"/usr/lib/systemd/system/pocketbase-{name}.service")
            if vendor_path.is_file():
                subprocess.run(
                    ["sudo", "rm", "-f", str(vendor_path)],
                    check=True,
                    capture_output=True,
                )
        except Exception:
            pass

        # Fix ownership of the entire instance dir so the hardened service
        # (User=pocketbase) can read hooks/migrations and write to pb_data.
        try:
            set_permissions(working_dir)
        except Exception:
            pass

        # Restart the instance so the new hardened service file takes effect
        try:
            from pocketmanager.core.systemd import restart_service

            restart_service(name)
        except Exception:
            pass

        # Configure auto-backup cron in PocketBase if enabled
        auto_backup_enabled = get("defaults.auto_backups_enabled", True)
        if auto_backup_enabled:
            try:
                from pocketmanager.core.backup import configure_auto_backup

                cron_expr = get("defaults.auto_backups_cron", "0 3 * * *")
                max_keep = get("defaults.auto_backups_max_keep", 7)
                configure_auto_backup(
                    f"http://127.0.0.1:{port}", cron_expr, max_keep,
                )
            except Exception:
                pass

        # Register in state
        instance_record: dict[str, Any] = {
            "name": name,
            "port": port,
            "version": detected_version,
            "instance_dir": working_dir,
            "domain": None,
            "env": env_vars,
            "pangolin_resource_id": None,
            "auto_backup": auto_backup_enabled,
            "backup_cron": get("defaults.auto_backups_cron", "0 3 * * *") if auto_backup_enabled else "",
            "backup_max_keep": get("defaults.auto_backups_max_keep", 7) if auto_backup_enabled else 0,
        }
        state_add_instance(instance_record)
        migrated.append(state_get_instance(name))  # type: ignore[arg-type]

    return migrated
