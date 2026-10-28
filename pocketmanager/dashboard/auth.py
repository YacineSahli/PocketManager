from __future__ import annotations

import functools
import hmac
import time

from flask import request, jsonify

# Brute-force protection: track failed attempts per client IP and lock out
# after too many. In-memory (resets on restart) but enough to make online
# password guessing impractical.
_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 300

_failures: dict[str, list] = {}  # ip -> [fail_count, locked_until_ts]


def check_auth(password: str) -> bool:
    """Check if the provided password matches the configured dashboard password."""
    # Load from config
    from pocketmanager.core.config import load_config

    config = load_config()
    dashboard_password = config.get("dashboard_password", "")
    if not dashboard_password:
        return False  # No password configured = deny access
    # Encode both sides — compare_digest raises TypeError on non-ASCII str.
    return hmac.compare_digest(
        password.encode("utf-8"), dashboard_password.encode("utf-8")
    )


def _client_ip() -> str:
    return request.remote_addr or "unknown"


def _is_locked_out(ip: str) -> bool:
    entry = _failures.get(ip)
    if not entry:
        return False
    count, locked_until = entry
    if locked_until and time.monotonic() < locked_until:
        return True
    if locked_until and time.monotonic() >= locked_until:
        # Lockout expired — reset
        del _failures[ip]
    return False


def _record_failure(ip: str) -> None:
    entry = _failures.get(ip) or [0, 0.0]
    entry[0] += 1
    if entry[0] >= _MAX_FAILURES:
        entry[1] = time.monotonic() + _LOCKOUT_SECONDS
        entry[0] = 0
    _failures[ip] = entry


def _record_success(ip: str) -> None:
    _failures.pop(ip, None)


def authenticate():
    """Send a 401 response that enables basic auth."""
    return jsonify({"error": "Authentication required"}), 401, {
        "WWW-Authenticate": 'Basic realm="PocketManager Dashboard"'
    }


def requires_auth(f):
    """Decorator to require auth for a route."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        ip = _client_ip()
        if _is_locked_out(ip):
            return jsonify({"error": "Too many failed attempts — try again later"}), 429

        auth = request.authorization
        if not auth or not check_auth(auth.password or ""):
            _record_failure(ip)
            return authenticate()
        _record_success(ip)
        return f(*args, **kwargs)
    return decorated
