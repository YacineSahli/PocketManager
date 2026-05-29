from __future__ import annotations

import functools
import hmac

from flask import request, jsonify


def check_auth(password: str) -> bool:
    """Check if the provided password matches the configured dashboard password."""
    # Load from config
    from pocketmanager.core.config import load_config

    config = load_config()
    dashboard_password = config.get("dashboard_password", "")
    if not dashboard_password:
        return False  # No password configured = deny access
    return hmac.compare_digest(password, dashboard_password)


def authenticate():
    """Send a 401 response that enables basic auth."""
    return jsonify({"error": "Authentication required"}), 401, {
        "WWW-Authenticate": 'Basic realm="PocketManager Dashboard"'
    }


def requires_auth(f):
    """Decorator to require auth for a route."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        from pocketmanager.core.config import load_config

        config = load_config()
        auth = request.authorization
        if not auth or not check_auth(auth.password or ""):
            return authenticate()
        return f(*args, **kwargs)
    return decorated
