"""Instance state management for PocketManager.

State is stored in ``instances.json`` in the state directory
(see :func:`pocketmanager.core.config.get_state_dir`).

All functions follow a **load → modify → save** pattern and are side-effect
free with respect to the caller's data (we deep-copy before mutating).
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pocketmanager.core.config import get_config_dir, get_state_dir


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_EMPTY_STATE: dict[str, Any] = {
    "version": 1,
    "instances": [],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fix_file_ownership(path: Path) -> None:
    """Ensure *path* is owned by the same uid:gid as its parent directory.

    This allows both root (via sudo) and the regular user to read/write
    the state file when ``POCKETMANAGER_HOME`` points to a shared location.
    """
    try:
        parent_stat = path.parent.stat()
        file_stat = path.stat()
        if file_stat.st_uid != parent_stat.st_uid or file_stat.st_gid != parent_stat.st_gid:
            os.chown(path, parent_stat.st_uid, parent_stat.st_gid)
    except (PermissionError, OSError):
        pass  # Non-root can't chown — that's fine


# ---------------------------------------------------------------------------
# File locking
# ---------------------------------------------------------------------------


@contextmanager
def _state_lock():
    """Exclusive file lock to prevent concurrent state file writes."""
    lock_path = get_state_dir() / "instances.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def _migrate_from_config_dir() -> None:
    """Move instances.json from config dir to state dir (one-time migration).

    In earlier versions, state lived alongside config in ``POCKETMANAGER_HOME``
    or ``~/.config/pocketmanager/``.  This moves it to the dedicated state
    directory on first access.
    """
    state_path = get_state_dir() / "instances.json"
    if state_path.is_file():
        return  # Already migrated or new install

    config_dir = get_config_dir()
    old_state = config_dir / "instances.json"
    if not old_state.is_file():
        return  # Nothing to migrate

    # Ensure target directory exists
    state_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old_state), str(state_path))

    # Also move the lock file if it exists
    old_lock = config_dir / "instances.json.lock"
    if old_lock.is_file():
        try:
            shutil.move(str(old_lock), str(get_state_dir() / "instances.json.lock"))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_state_path() -> Path:
    """Return the path to ``instances.json``."""
    _migrate_from_config_dir()
    return get_state_dir() / "instances.json"


def load_state() -> dict[str, Any]:
    """Load instance state from disk.

    Returns an empty-state skeleton when the file does not exist.
    """
    import copy

    path = get_state_path()
    if path.is_file():
        with path.open("r", encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
        return data
    return copy.deepcopy(_EMPTY_STATE)


def save_state(state: dict[str, Any]) -> None:
    """Atomically write *state* to ``instances.json``.

    Uses a temporary file + rename to avoid corrupt state on crash.
    """
    path = get_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix="state_",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_path, path)
        # Match parent dir ownership so both root and regular user can access
        _fix_file_ownership(path)
        os.chmod(path, 0o660)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Instance helpers
# ---------------------------------------------------------------------------


def get_all_instances() -> list[dict[str, Any]]:
    """Return all registered instances."""
    return load_state().get("instances", [])


def get_instance(name: str) -> dict[str, Any] | None:
    """Find an instance by *name* (case-insensitive)."""
    name_lower = name.lower()
    for inst in load_state().get("instances", []):
        if inst.get("name", "").lower() == name_lower:
            return inst
    return None


def add_instance(instance: dict[str, Any]) -> None:
    """Register a new instance.

    Auto-populates:

    * ``slug`` — ``"pocketbase-{name}"``
    * ``created_at`` — ISO-8601 UTC timestamp
    """
    import copy

    with _state_lock():
        state = load_state()

        inst = copy.deepcopy(instance)
        name: str = inst.get("name", "unnamed")
        inst.setdefault("slug", f"pocketbase-{name}")
        inst.setdefault(
            "created_at",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        state.setdefault("instances", []).append(inst)
        save_state(state)


def remove_instance(name: str) -> dict[str, Any] | None:
    """Remove an instance by *name* (case-insensitive).

    Returns the removed instance dict, or ``None`` if not found.
    """
    with _state_lock():
        state = load_state()
        instances: list[dict[str, Any]] = state.get("instances", [])
        name_lower = name.lower()

        for idx, inst in enumerate(instances):
            if inst.get("name", "").lower() == name_lower:
                removed = instances.pop(idx)
                save_state(state)
                return removed

    return None


def update_instance(
    name: str,
    updates: dict[str, Any],
) -> dict[str, Any] | None:
    """Update specific fields of an instance.

    Returns the updated instance dict, or ``None`` if not found.
    """
    with _state_lock():
        state = load_state()
        instances: list[dict[str, Any]] = state.get("instances", [])
        name_lower = name.lower()

        for inst in instances:
            if inst.get("name", "").lower() == name_lower:
                inst.update(updates)
                save_state(state)
                return inst

    return None
