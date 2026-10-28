from __future__ import annotations

from flask import Blueprint, request, jsonify

from pocketmanager.dashboard.auth import requires_auth

api = Blueprint("api", __name__)


def _strip_instance_secrets(instance: dict) -> dict:
    """Remove sensitive fields from an instance dict before sending to the client."""
    safe = dict(instance)
    safe.pop("superadmin_email", None)
    safe.pop("superadmin_password", None)
    return safe


@api.before_request
def _check_csrf():
    """Block CSRF attacks on state-changing endpoints.

    Requires ``Content-Type: application/json`` which HTML forms cannot set
    (they use ``application/x-www-form-urlencoded`` or ``multipart/form-data``).
    Combined with the browser's same-origin policy and lack of CORS headers on
    this API, this is sufficient to block all browser-based CSRF attacks.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None

    ct = request.content_type or ""
    if "application/json" not in ct:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    return None


# GET /api/instances - List all instances with status
@api.route("/instances", methods=["GET"])
@requires_auth
def list_instances():
    from pocketmanager.core.instance import list_instances as li

    instances = li()
    return jsonify({"instances": [_strip_instance_secrets(i) for i in instances]})


# GET /api/instances/<name> - Get detailed info
@api.route("/instances/<name>", methods=["GET"])
@requires_auth
def get_instance(name):
    from pocketmanager.core.instance import get_instance_info

    try:
        info = get_instance_info(name)
        return jsonify(_strip_instance_secrets(info))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


# POST /api/instances - Create new instance
@api.route("/instances", methods=["POST"])
@requires_auth
def create_instance():
    from pocketmanager.core.instance import create_instance

    data = request.get_json() or {}
    name = data.get("name")
    if not name:
        return jsonify({"error": "Field 'name' is required"}), 400

    domain = data.get("domain")
    if not domain and data.get("subdomain"):
        from pocketmanager.core.config import get

        default_domain = get("pangolin.default_domain", "")
        if not default_domain:
            return jsonify(
                {
                    "error": (
                        "Subdomain given but pangolin.default_domain is not "
                        "configured — set it or provide a full domain"
                    )
                }
            ), 400
        domain = f"{data['subdomain']}.{default_domain}"

    try:
        instance = create_instance(
            name=name,
            port=data.get("port"),
            domain=domain,
            env=data.get("env"),
            version=data.get("version"),
            pangolin=data.get("pangolin", True),
        )
        return jsonify(_strip_instance_secrets(instance)), 201
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 400


# DELETE /api/instances/<name> - Remove instance
@api.route("/instances/<name>", methods=["DELETE"])
@requires_auth
def remove_instance(name):
    from pocketmanager.core.instance import remove_instance

    data = request.get_json() or {}
    try:
        removed = remove_instance(
            name=name,
            keep_data=data.get("keep_data", False),
            remove_pangolin=data.get("remove_pangolin", True),
        )
        return jsonify(_strip_instance_secrets(removed))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


# POST /api/instances/<name>/start
@api.route("/instances/<name>/start", methods=["POST"])
@requires_auth
def start_instance(name):
    from pocketmanager.core.instance import start_instance

    try:
        result = start_instance(name)
        if result:
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "Service failed to start"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500


# POST /api/instances/<name>/stop
@api.route("/instances/<name>/stop", methods=["POST"])
@requires_auth
def stop_instance(name):
    from pocketmanager.core.instance import stop_instance

    try:
        stop_instance(name)
        return jsonify({"success": True})
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 404 if isinstance(e, ValueError) else 500


# POST /api/instances/<name>/restart
@api.route("/instances/<name>/restart", methods=["POST"])
@requires_auth
def restart_instance(name):
    from pocketmanager.core.instance import restart_instance

    try:
        result = restart_instance(name)
        if result:
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "Service failed to restart"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500


# GET /api/instances/<name>/logs
@api.route("/instances/<name>/logs", methods=["GET"])
@requires_auth
def get_logs(name):
    from pocketmanager.core.state import get_instance
    from pocketmanager.core.systemd import get_journal_logs

    if not get_instance(name):
        return jsonify({"error": "Unknown instance"}), 404

    lines = request.args.get("lines", 100, type=int)
    lines = min(max(lines, 1), 1000)  # cap — don't stream the whole journal
    logs = get_journal_logs(name, lines=lines)
    return jsonify({"logs": logs})


# GET /api/instances/<name>/health - Health check a single instance
@api.route("/instances/<name>/health", methods=["GET"])
@requires_auth
def instance_health(name):
    from pocketmanager.core.health import check_instance_health
    from pocketmanager.core.state import get_instance

    inst = get_instance(name)
    if not inst:
        return jsonify({"error": "Instance not found"}), 404
    try:
        return jsonify(check_instance_health(inst["port"]))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# GET /api/instances/<name>/backups
@api.route("/instances/<name>/backups", methods=["GET"])
@requires_auth
def list_backups(name):
    from pocketmanager.core.backup import (
        get_instance_auth_token,
        list_backups as pb_list_backups,
    )
    from pocketmanager.core.state import get_instance

    inst = get_instance(name)
    if not inst:
        return jsonify({"error": "Instance not found"}), 404
    if not inst.get("superadmin_email") or not inst.get("superadmin_password"):
        # No stored credentials — answer without hitting the instance.
        return jsonify({"backups": [], "auth": False, "error": "credentials_required"})
    url = f"http://localhost:{inst['port']}"
    token = get_instance_auth_token(name)
    if token is None:
        return jsonify({"backups": [], "auth": False, "error": "credentials_required"})
    backups = pb_list_backups(url, auth_token=token)
    return jsonify({"backups": backups or [], "auth": True})


# POST /api/instances/<name>/backup
@api.route("/instances/<name>/backup", methods=["POST"])
@requires_auth
def create_backup(name):
    from pocketmanager.core.backup import (
        create_backup as pb_create_backup,
        get_instance_auth_token,
    )
    from pocketmanager.core.state import get_instance

    inst = get_instance(name)
    if not inst:
        return jsonify({"error": "Instance not found"}), 404
    url = f"http://localhost:{inst['port']}"
    token = get_instance_auth_token(name)
    if not token:
        return jsonify({"error": "No PocketBase superadmin credentials configured"}), 403
    data = request.get_json() or {}
    success = pb_create_backup(url, name=data.get("name"), auth_token=token)
    return jsonify({"success": success})


# GET /api/health - Health check all instances
@api.route("/health", methods=["GET"])
@requires_auth
def health_check():
    from pocketmanager.core.health import check_all_instances

    results = check_all_instances()
    return jsonify({"results": results})


# GET /api/config - Get config
@api.route("/config", methods=["GET"])
@requires_auth
def get_config():
    from pocketmanager.core.config import load_config

    config = load_config()
    # Don't expose sensitive fields
    safe = {k: v for k, v in config.items() if k not in ("pangolin", "sftp", "dashboard_password")}
    safe["dashboard_password"] = "***" if config.get("dashboard_password") else ""
    safe["pangolin"] = {
        k: ("***" if k in ("api_key",) and v else v)
        for k, v in config.get("pangolin", {}).items()
    }
    safe["sftp"] = {
        k: ("***" if k in ("password",) and v else v)
        for k, v in config.get("sftp", {}).items()
    }
    return jsonify(safe)


# POST /api/instances/<name>/credentials - Set PocketBase superadmin credentials
@api.route("/instances/<name>/credentials", methods=["POST"])
@requires_auth
def set_credentials(name):
    from pocketmanager.core.backup import authenticate
    from pocketmanager.core.state import get_instance, update_instance

    inst = get_instance(name)
    if not inst:
        return jsonify({"error": "Instance not found"}), 404

    data = request.get_json() or {}
    email = data.get("email", "").strip()
    password = data.get("password", "")
    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    url = f"http://localhost:{inst['port']}"
    token = authenticate(url, email, password)
    if not token:
        return jsonify({"error": "Authentication failed — check email and password"}), 401

    update_instance(name, {"superadmin_email": email, "superadmin_password": password})
    return jsonify({"success": True})
