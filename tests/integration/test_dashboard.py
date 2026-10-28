"""Dashboard tests — auth, CSRF gate, secret masking, security headers.

These tests run anywhere (no Linux binary, no systemd): they use the
isolated_env fixture and Flask's test client with the systemd layer patched.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from pocketmanager.core import config as config_mod
from pocketmanager.core import systemd as systemd_mod
from pocketmanager.dashboard import auth as auth_mod
from pocketmanager.dashboard.app import create_app

PASSWORD = "correct-horse-battery"


@pytest.fixture()
def dash_app(isolated_env):
    """Flask app with a dashboard password configured and systemd mocked."""
    config = config_mod.load_config()
    config["dashboard_password"] = PASSWORD
    config["sftp"] = {
        "enabled": True,
        "host": "box.example.com",
        "port": 23,
        "username": "u1",
        "password": "s3cret-sftp-password",
        "private_key_path": "",
        "remote_path": "backups",
        "max_remote_backups": 30,
    }
    config_mod.save_config(config)

    app = create_app()
    app.config["TESTING"] = True

    # Reset the in-memory rate-limit tracker between tests
    auth_mod._failures.clear()

    with patch.object(systemd_mod, "is_active", MagicMock(return_value=False)):
        yield app


@pytest.fixture()
def client(dash_app):
    return dash_app.test_client()


def _auth(password: str = PASSWORD):
    token = base64.b64encode(f"admin:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_no_password_configured_denies_all(isolated_env):
    """A dashboard without a password must fail closed."""
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    resp = client.get("/api/instances")
    assert resp.status_code == 401


def test_wrong_password_rejected(client):
    resp = client.get("/api/instances", headers=_auth("wrong"))
    assert resp.status_code == 401


def test_correct_password_accepted(client):
    resp = client.get("/api/instances", headers=_auth())
    assert resp.status_code == 200
    assert resp.json == {"instances": []}


def test_non_ascii_password_no_500(client):
    """Non-ASCII Basic credentials must not crash compare_digest."""
    resp = client.get("/api/instances", headers=_auth("pässwörd"))
    assert resp.status_code == 401


def test_brute_force_lockout(client):
    """Five failed attempts lock the IP out with 429."""
    for _ in range(5):
        resp = client.get("/api/instances", headers=_auth("nope"))
        assert resp.status_code == 401
    resp = client.get("/api/instances", headers=_auth(PASSWORD))
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# CSRF content-type gate
# ---------------------------------------------------------------------------


def test_post_without_json_content_type_rejected(client):
    resp = client.post(
        "/api/instances",
        headers={**_auth(), "Content-Type": "application/x-www-form-urlencoded"},
        data={"name": "x"},
    )
    assert resp.status_code == 415


def test_delete_still_requires_json(client):
    resp = client.delete("/api/instances/foo", headers=_auth())
    assert resp.status_code == 415


# ---------------------------------------------------------------------------
# Secret masking
# ---------------------------------------------------------------------------


def test_config_masks_secrets(client):
    resp = client.get("/api/config", headers=_auth())
    assert resp.status_code == 200
    body = json.dumps(resp.json)
    assert "s3cret-sftp-password" not in body
    assert resp.json["sftp"]["password"] == "***"
    assert resp.json["dashboard_password"] == "***"


def test_instance_list_strips_superadmin_credentials(client):
    from pocketmanager.core import state as state_mod

    state_mod.add_instance({
        "name": "myapp",
        "port": 8090,
        "superadmin_email": "admin@x",
        "superadmin_password": "super-secret",
    })
    resp = client.get("/api/instances", headers=_auth())
    assert resp.status_code == 200
    body = json.dumps(resp.json)
    assert "super-secret" not in body
    assert "superadmin_password" not in body


# ---------------------------------------------------------------------------
# Input validation & headers
# ---------------------------------------------------------------------------


def test_create_instance_missing_name_returns_400(client):
    resp = client.post(
        "/api/instances", headers=_auth(), json={"port": 8090}
    )
    assert resp.status_code == 400
    assert "name" in resp.json["error"]


def test_logs_lines_param_capped(client):
    """lines is clamped, so journalctl can't be asked for the whole journal."""
    with patch.object(
        systemd_mod, "get_journal_logs", MagicMock(return_value="...")
    ) as logs:
        resp = client.get(
            "/api/instances/myapp/logs?lines=100000000", headers=_auth()
        )
        assert resp.status_code == 200
        assert logs.call_args.kwargs.get("lines") <= 1000


def test_security_headers_on_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in resp.headers.get("Content-Security-Policy", "")


def test_template_has_no_name_interpolating_inline_handlers(client):
    """The card rendering must use data-action delegation, not inline JS."""
    html = (
        client.get("/").data.decode("utf-8")
    )
    assert "toggleCard('" not in html
    assert "data-action=" in html
