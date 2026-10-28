from __future__ import annotations

from flask import Flask, send_from_directory
from pathlib import Path


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
    )

    from pocketmanager.dashboard.api import api

    app.register_blueprint(api, url_prefix="/api")

    @app.after_request
    def _security_headers(response):
        # Modest hardening: blocks framing, MIME sniffing, and (via
        # connect-src) data exfiltration from any injected script.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'",
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.route("/")
    def index():
        return send_from_directory(
            str(Path(__file__).parent / "templates"), "dashboard.html"
        )

    return app


def run_server(host: str = "127.0.0.1", port: int = 8888, debug: bool = False):
    app = create_app()
    app.run(host=host, port=port, debug=debug)
