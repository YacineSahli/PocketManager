"""Integration tests for PocketManager core modules: config, state, and ports.

These tests exercise the real file system (via temp directories) and verify
that config loading, state management, and port allocation work correctly.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    """Tests for pocketmanager.core.config."""

    def test_load_default_config(self, isolated_env):
        """Loaded config should contain all default keys."""
        from pocketmanager.core.config import load_config

        config = load_config()

        # Top-level keys
        assert "base_dir" in config
        assert "cache_dir" in config
        assert "dashboard_port" in config
        assert "dashboard_password" in config
        assert "port_range" in config
        assert "pangolin" in config
        assert "defaults" in config

        # Nested pangolin keys
        pangolin = config["pangolin"]
        for key in (
            "dashboard_url",
            "api_url",
            "api_key",
            "org_id",
            "default_domain_id",
            "default_domain",
            "subdomain_suffix",
            "site_id",
            "target_ip",
        ):
            assert key in pangolin, f"pangolin.{key} missing"

        # Nested defaults keys
        defaults = config["defaults"]
        for key in ("auto_backups_enabled", "auto_backups_cron", "auto_backups_max_keep"):
            assert key in defaults, f"defaults.{key} missing"

    def test_get_nested_value(self, isolated_env):
        """get() with dot notation should retrieve nested values."""
        from pocketmanager.core.config import get

        # The isolated_env fixture writes a config with pangolin.target_ip = "127.0.0.1"
        assert get("pangolin.target_ip") == "127.0.0.1"

    def test_get_with_default(self, isolated_env):
        """get() should return the provided default for missing keys."""
        from pocketmanager.core.config import get

        assert get("nonexistent.key") is None
        assert get("nonexistent.key", "fallback") == "fallback"
        assert get("deeply.nested.missing", 42) == 42

    def test_set_value(self, isolated_env):
        """set() should persist a simple top-level value."""
        from pocketmanager.core.config import get, set

        set("dashboard_password", "test123")
        assert get("dashboard_password") == "test123"

    def test_set_nested_value(self, isolated_env):
        """set() should persist a value nested via dot notation."""
        from pocketmanager.core.config import get, set

        set("pangolin.api_key", "secret")
        assert get("pangolin.api_key") == "secret"

    def test_save_and_reload(self, isolated_env):
        """Config changes should survive a save/reload cycle."""
        from pocketmanager.core.config import get, load_config, save_config

        config = load_config()
        config["dashboard_password"] = "persisted"
        save_config(config)

        # Reload from disk in a fresh call
        from pocketmanager.core.config import load_config as load_config_again

        reloaded = load_config_again()
        assert reloaded["dashboard_password"] == "persisted"

    def test_get_config_dir(self, isolated_env):
        """get_config_dir() should return the POCKETMANAGER_HOME path."""
        from pocketmanager.core.config import get_config_dir

        expected = Path(os.environ["POCKETMANAGER_HOME"]).resolve()
        assert get_config_dir() == expected

    def test_config_file_permissions(self, isolated_env):
        """After save_config(), the file should have 0o660 permissions."""
        import stat

        from pocketmanager.core.config import get_config_path, load_config, save_config

        config = load_config()
        save_config(config)

        mode = os.stat(get_config_path()).st_mode
        perms = stat.S_IMODE(mode)
        assert perms == 0o660


# ---------------------------------------------------------------------------
# State tests
# ---------------------------------------------------------------------------


class TestState:
    """Tests for pocketmanager.core.state."""

    def test_empty_state(self, isolated_env):
        """Fresh environment should have an empty instances list."""
        from pocketmanager.core.state import get_all_instances

        assert get_all_instances() == []

    def test_add_instance(self, isolated_env):
        """Adding an instance should make it appear in get_all_instances()."""
        from pocketmanager.core.state import add_instance, get_all_instances

        add_instance({"name": "myapp", "port": 9090})

        instances = get_all_instances()
        assert len(instances) == 1
        assert instances[0]["name"] == "myapp"
        assert instances[0]["port"] == 9090

    def test_add_instance_auto_populates(self, isolated_env):
        """add_instance() should auto-set slug and created_at."""
        from pocketmanager.core.state import add_instance, get_all_instances

        add_instance({"name": "autopop", "port": 9091})

        inst = get_all_instances()[0]
        assert "slug" in inst
        assert inst["slug"] == "pocketbase-autopop"
        assert "created_at" in inst
        # Should be a valid ISO-8601 UTC timestamp (e.g. "2026-05-29T12:34:56Z")
        assert inst["created_at"].endswith("Z")

    def test_get_instance(self, isolated_env):
        """get_instance() should return the matching instance dict."""
        from pocketmanager.core.state import add_instance, get_instance

        add_instance({"name": "lookup-test", "port": 9092})

        inst = get_instance("lookup-test")
        assert inst is not None
        assert inst["name"] == "lookup-test"
        assert inst["port"] == 9092

    def test_get_instance_case_insensitive(self, isolated_env):
        """get_instance() should match case-insensitively."""
        from pocketmanager.core.state import add_instance, get_instance

        add_instance({"name": "TestApp", "port": 9093})

        assert get_instance("testapp") is not None
        assert get_instance("TESTAPP") is not None
        assert get_instance("tEsTaPp") is not None
        # All should return the same instance
        assert get_instance("testapp")["name"] == "TestApp"

    def test_get_instance_not_found(self, isolated_env):
        """get_instance() should return None for unknown names."""
        from pocketmanager.core.state import get_instance

        assert get_instance("nonexistent") is None
        assert get_instance("") is None

    def test_remove_instance(self, isolated_env):
        """remove_instance() should delete the instance from state."""
        from pocketmanager.core.state import (
            add_instance,
            get_all_instances,
            get_instance,
            remove_instance,
        )

        add_instance({"name": "removable", "port": 9094})

        removed = remove_instance("removable")
        assert removed is not None
        assert removed["name"] == "removable"

        # Verify it's gone
        assert get_all_instances() == []
        assert get_instance("removable") is None

    def test_remove_instance_not_found(self, isolated_env):
        """remove_instance() should return None when the name doesn't exist."""
        from pocketmanager.core.state import remove_instance

        assert remove_instance("nonexistent") is None

    def test_update_instance(self, isolated_env):
        """update_instance() should apply changes and return the updated dict."""
        from pocketmanager.core.state import add_instance, get_instance, update_instance

        add_instance({"name": "updatable", "port": 9095})

        updated = update_instance("updatable", {"port": 9999, "version": "0.25.0"})
        assert updated is not None
        assert updated["port"] == 9999
        assert updated["version"] == "0.25.0"

        # Persisted on disk
        inst = get_instance("updatable")
        assert inst["port"] == 9999
        assert inst["version"] == "0.25.0"

    def test_update_instance_not_found(self, isolated_env):
        """update_instance() should return None for unknown names."""
        from pocketmanager.core.state import update_instance

        assert update_instance("nonexistent", {"port": 1234}) is None

    def test_multiple_instances(self, isolated_env):
        """Multiple instances should all be retrievable individually and collectively."""
        from pocketmanager.core.state import (
            add_instance,
            get_all_instances,
            get_instance,
        )

        names = ["alpha", "beta", "gamma"]
        ports = [9096, 9097, 9098]
        for name, port in zip(names, ports):
            add_instance({"name": name, "port": port})

        all_instances = get_all_instances()
        assert len(all_instances) == 3

        # Each should be individually retrievable
        for name, port in zip(names, ports):
            inst = get_instance(name)
            assert inst is not None
            assert inst["name"] == name
            assert inst["port"] == port

    def test_state_file_permissions(self, isolated_env):
        """After saving state, instances.json should have group-readable (0o660) permissions."""
        import stat

        from pocketmanager.core.state import add_instance, get_state_path

        add_instance({"name": "perms-test", "port": 9099})

        mode = os.stat(get_state_path()).st_mode
        perms = stat.S_IMODE(mode)
        assert perms == 0o660


# ---------------------------------------------------------------------------
# Port tests
# ---------------------------------------------------------------------------


class TestPorts:
    """Tests for pocketmanager.core.ports."""

    def test_find_available_port(self, isolated_env):
        """find_available_port() should return a port within the configured range."""
        from pocketmanager.core.ports import find_available_port

        port = find_available_port()
        assert 9090 <= port <= 9999

    def test_is_port_free_unallocated(self, isolated_env):
        """A high random port with no instances allocated should be free."""
        from pocketmanager.core.ports import is_port_free

        # Pick a port well outside the configured range and unlikely to be in use
        assert is_port_free(59999) is True

    def test_allocated_ports_empty(self, isolated_env):
        """With no instances, get_allocated_ports() should return an empty set."""
        from pocketmanager.core.ports import get_allocated_ports

        assert get_allocated_ports() == set()

    def test_allocated_ports_with_instances(self, isolated_env):
        """get_allocated_ports() should return ports from all instances."""
        from pocketmanager.core.ports import get_allocated_ports
        from pocketmanager.core.state import add_instance

        add_instance({"name": "port-a", "port": 9100})
        add_instance({"name": "port-b", "port": 9101})

        allocated = get_allocated_ports()
        assert 9100 in allocated
        assert 9101 in allocated
        assert len(allocated) == 2

    def test_port_excluded_when_allocated(self, isolated_env):
        """is_port_free() should return False for a port allocated to an instance."""
        from pocketmanager.core.ports import is_port_free
        from pocketmanager.core.state import add_instance

        # Use a port outside the typical system-used range to avoid
        # false negatives from ss output.
        test_port = 59100
        add_instance({"name": "port-holder", "port": test_port})

        assert is_port_free(test_port) is False

    def test_find_port_skips_allocated(self, isolated_env):
        """find_available_port() should skip ports already allocated in state."""
        from pocketmanager.core.ports import find_available_port
        from pocketmanager.core.state import add_instance

        # Allocate ports 9090-9094 so find_available_port must start at 9095 or later
        for port in range(9090, 9095):
            add_instance({"name": f"filler-{port}", "port": port})

        port = find_available_port()
        assert port >= 9095
