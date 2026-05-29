from __future__ import annotations

from flask import Flask, send_from_directory
from pathlib import Path


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
    )

    from pocketmanager.dashboard.api import api

    app.register_blueprint(api)

    @app.route("/")
    def index():
        return send_from_directory(
            str(Path(__file__).parent / "templates"), "dashboard.html"
        )

    return app


def run_server(host: str = "0.0.0.0", port: int = 8888, debug: bool = False):
    app = create_app()
    app.run(host=host, port=port, debug=debug)
