"""Local web app. Runs on 127.0.0.1 only; all data stays on this machine.

Start it with `python -m taste app` (or double-click the packaged exe).
"""

from __future__ import annotations

import hmac
import secrets
from pathlib import Path
from typing import Any

from flask import Flask, abort, request, session

from taste import __version__
from taste.config import data_dir
from taste.secrets_store import SecretStore
from taste.web.jobs import JobManager

# Requests must name this machine in the Host header. Blocks DNS rebinding, where a
# website points its own domain at 127.0.0.1 to reach local apps.
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


def _secret_key() -> str:
    """A random key for signing the session cookie, created once and kept locally."""
    path = data_dir() / ".secret_key"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    return key


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def create_app(
    *,
    store: SecretStore | None = None,
    client_factory: Any = None,
    sync_kwargs: dict[str, Any] | None = None,
    secret_key: str | None = None,
) -> Flask:
    """Build the app. Tests pass fakes for the key store and HTTP clients."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = secret_key or _secret_key()
    app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

    store = store or SecretStore()
    app.extensions["taste"] = {
        "store": store,
        "jobs": JobManager(store, client_factory, sync_kwargs),
        "prepared": set(),  # profile DB paths whose schema was set up this run
    }

    @app.before_request
    def protect() -> None:
        host = (request.host or "").rsplit(":", 1)[0].strip("[]")
        if host not in ALLOWED_HOSTS:
            abort(400, "This app only answers requests for 127.0.0.1 or localhost.")
        if request.method == "POST":
            sent = request.form.get("csrf_token", "")
            if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
                abort(400, "The form expired or came from another site. Reload and try again.")

    @app.after_request
    def headers(response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.context_processor
    def template_globals() -> dict[str, Any]:
        return {"csrf_token": csrf_token, "version": __version__}

    from taste.web import routes

    app.register_blueprint(routes.bp)
    return app


def serve(port: int = 8765, open_browser: bool = True) -> None:
    """Run the app until Ctrl+C or the console window is closed."""
    import threading
    import webbrowser

    from werkzeug.serving import make_server

    from taste import profiles

    if profiles.migrate_legacy():
        print("Moved data/taste.db to the 'default' profile.")
    app = create_app()
    server = make_server("127.0.0.1", port, app, threaded=True)
    url = f"http://127.0.0.1:{port}/"
    print(f"Taste Engine {__version__} is running at {url}")
    print(f"Data folder: {Path(data_dir()).resolve()}")
    print("Close this window (or press Ctrl+C) to stop.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
