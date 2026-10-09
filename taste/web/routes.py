"""Pages and form handlers."""

from __future__ import annotations

import csv
import io
import os
import sqlite3
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from taste import __main__ as cli
from taste import db, profiles, reports, runner, settings
from taste.config import data_dir
from taste.profiles import ProfileError
from taste.secrets_store import KEY_LABELS, KEY_NAMES, SecretStoreError
from taste.settings import SettingError

bp = Blueprint("web", __name__)

KEY_SOURCES = {"MAL_CLIENT_ID": "mal", "LASTFM_API_KEY": "lastfm"}


def _ext() -> dict:
    return current_app.extensions["taste"]


@contextmanager
def profile_db(profile_id: str) -> Iterator[sqlite3.Connection]:
    """Open a profile's database for one request. 404 if the profile doesn't exist."""
    try:
        path = profiles.db_path(profile_id)
    except ProfileError:
        abort(404)
    if not path.is_file():
        abort(404)
    prepared = _ext()["prepared"]
    conn = db.connect(path, setup=str(path) not in prepared)
    prepared.add(str(path))
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


@bp.get("/")
def home():
    rows = []
    for p in profiles.list_profiles():
        with profile_db(p.profile_id) as conn:
            last = conn.execute(
                "SELECT MAX(ended_at) FROM sync_runs WHERE status = 'success'"
            ).fetchone()[0]
        rows.append({"id": p.profile_id, "name": p.display_name, "last_sync": last})
    return render_template("home.html", profiles=rows)


@bp.post("/profiles")
def create_profile():
    profile_id = request.form.get("profile_id", "").strip().lower()
    name = request.form.get("display_name", "").strip()
    try:
        profiles.create(profile_id, name)
    except (ProfileError, SettingError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("web.home"))
    flash("Profile created. Add your usernames and API keys to get started.", "ok")
    return redirect(url_for("web.settings_page", profile_id=profile_id))


@bp.post("/p/<profile_id>/delete")
def delete_profile(profile_id: str):
    with profile_db(profile_id):
        pass  # 404 if missing
    if _ext()["jobs"].is_running(profile_id):
        flash("Wait for the running sync to finish before deleting this profile.", "error")
        return redirect(url_for("web.settings_page", profile_id=profile_id))
    if request.form.get("confirm", "").strip() != profile_id:
        flash(f"To delete, type the profile ID ({profile_id}) exactly.", "error")
        return redirect(url_for("web.settings_page", profile_id=profile_id))
    profiles.delete(profile_id, store=_ext()["store"])
    _ext()["prepared"].discard(str(profiles.db_path(profile_id)))
    flash(f"Deleted profile {profile_id}, its data, and its stored keys.", "ok")
    return redirect(url_for("web.home"))


# ---------------------------------------------------------------------------
# Dashboard and sync
# ---------------------------------------------------------------------------


@bp.get("/p/<profile_id>/")
def dashboard(profile_id: str):
    with profile_db(profile_id) as conn:
        status = cli.status_lines(conn)
        counts = {
            "MAL list entries": _count(conn, "stg_mal_list_entries WHERE removed_at IS NULL"),
            "Scored anime": _count(conn, "core_ratings WHERE source = 'mal'"),
            "Last.fm plays": _count(conn, "core_behavior_events WHERE source = 'lastfm'"),
        }
        open_windows = _count(conn, "sync_lastfm_windows WHERE status = 'open'")
        name = settings.get(conn, "display_name") or profile_id
    return render_template(
        "dashboard.html",
        profile_id=profile_id,
        name=name,
        status=status,
        counts=counts,
        open_windows=open_windows,
        running=_ext()["jobs"].is_running(profile_id),
    )


def _count(conn: sqlite3.Connection, table_and_filter: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table_and_filter}").fetchone()[0]


@bp.post("/p/<profile_id>/sync")
def start_sync(profile_id: str):
    with profile_db(profile_id):
        pass
    source = request.form.get("source", "")
    if source not in ("mal", "lastfm", "all"):
        abort(400)
    try:
        _ext()["jobs"].start(profile_id, source)
    except RuntimeError as exc:
        flash(str(exc), "error")
    return redirect(url_for("web.sync_page", profile_id=profile_id))


@bp.get("/p/<profile_id>/sync")
def sync_page(profile_id: str):
    with profile_db(profile_id) as conn:
        name = settings.get(conn, "display_name") or profile_id
    job = _ext()["jobs"].get(profile_id)
    return render_template(
        "sync.html", profile_id=profile_id, name=name, job=job.snapshot() if job else None
    )


@bp.get("/p/<profile_id>/sync/status")
def sync_status(profile_id: str):
    with profile_db(profile_id):
        pass
    job = _ext()["jobs"].get(profile_id)
    return jsonify(job.snapshot() if job else {"running": False, "messages": []})


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def _report_rows(conn: sqlite3.Connection, report: reports.Report):
    cursor = conn.execute(f"SELECT * FROM {report.view} ORDER BY {report.order_by}")
    return [d[0] for d in cursor.description], cursor.fetchall()


@bp.get("/p/<profile_id>/reports")
def reports_page(profile_id: str):
    with profile_db(profile_id) as conn:
        summary = reports.summary_lines(conn)
        tables = []
        for report in reports.REPORTS:
            columns, rows = _report_rows(conn, report)
            tables.append(
                {
                    "name": report.csv_name.removesuffix(".csv"),
                    "title": report.title,
                    "columns": columns,
                    "rows": rows,
                    "lastfm": report.uses_lastfm,
                }
            )
        hours = conn.execute(
            "SELECT local_hour AS label, plays, pct_of_plays FROM rpt_lastfm_by_hour "
            "ORDER BY local_hour"
        ).fetchall()
        days = conn.execute(
            "SELECT weekday AS label, plays, pct_of_plays FROM rpt_lastfm_by_weekday "
            "ORDER BY local_weekday_num"
        ).fetchall()
        hours, days = _with_widths(hours), _with_widths(days)
        scope = conn.execute("SELECT * FROM rpt_lastfm_scope").fetchone()
        tz_name = settings.get(conn, "timezone")
        name = settings.get(conn, "display_name") or profile_id
    return render_template(
        "reports.html",
        profile_id=profile_id,
        name=name,
        summary=summary,
        tables=tables,
        hours=hours,
        days=days,
        scope=scope,
        tz_name=tz_name,
    )


def _with_widths(rows) -> list[dict]:
    """Bar widths scaled so the busiest bar fills the row."""
    top = max((r["plays"] for r in rows), default=0) or 1
    return [{**dict(r), "width": round(100 * r["plays"] / top, 1)} for r in rows]


def _csv_text(columns: list[str], rows) -> str:
    buffer = io.StringIO()
    # BOM so Excel shows non-English titles correctly, same as the CLI's CSVs.
    buffer.write("﻿")
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows(tuple(r) for r in rows)
    return buffer.getvalue()


@bp.get("/p/<profile_id>/reports/<name>.csv")
def report_csv(profile_id: str, name: str):
    report = next((r for r in reports.REPORTS if r.csv_name == f"{name}.csv"), None)
    if report is None:
        abort(404)
    with profile_db(profile_id) as conn:
        columns, rows = _report_rows(conn, report)
    return Response(
        _csv_text(columns, rows),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={profile_id}-{name}.csv"},
    )


@bp.get("/p/<profile_id>/reports.zip")
def reports_zip(profile_id: str):
    buffer = io.BytesIO()
    with profile_db(profile_id) as conn, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for report in reports.REPORTS:
            columns, rows = _report_rows(conn, report)
            zf.writestr(report.csv_name, _csv_text(columns, rows))
    return Response(
        buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename={profile_id}-reports.zip"},
    )


# ---------------------------------------------------------------------------
# Settings and keys
# ---------------------------------------------------------------------------


def _key_status(profile_id: str) -> list[dict]:
    store = _ext()["store"]
    rows = []
    for name in KEY_NAMES:
        try:
            _, source = store.get_with_source(profile_id, name)
            error = None
        except SecretStoreError as exc:
            source, error = None, str(exc)
        rows.append(
            {
                "name": name,
                "label": KEY_LABELS[name],
                "source": source,
                "stored": source in ("keyring", "file"),
                "error": error,
                "test_source": KEY_SOURCES[name],
            }
        )
    return rows


@bp.get("/p/<profile_id>/settings")
def settings_page(profile_id: str):
    with profile_db(profile_id) as conn:
        values = settings.get_all(conn)
    return render_template(
        "settings.html",
        profile_id=profile_id,
        name=values["display_name"] or profile_id,
        values=values,
        fields=settings.SETTINGS,
        timezones=settings.timezone_names(),
        keys=_key_status(profile_id),
        secure=_ext()["store"].secure,
        data_folder=str(data_dir().resolve()),
        running=_ext()["jobs"].is_running(profile_id),
    )


@bp.post("/p/<profile_id>/settings")
def save_settings(profile_id: str):
    errors = []
    with profile_db(profile_id) as conn:
        for field in settings.SETTINGS:
            if field.kind == "bool":
                raw = request.form.get(field.key) == "on"
            elif field.key in request.form:
                raw = request.form[field.key]
            else:
                continue
            try:
                if raw != settings.get(conn, field.key):
                    settings.set_value(conn, field.key, raw)
            except SettingError as exc:
                errors.append(str(exc))
    for error in errors:
        flash(error, "error")
    if not errors:
        flash("Settings saved.", "ok")
    return redirect(url_for("web.settings_page", profile_id=profile_id))


@bp.post("/p/<profile_id>/keys/<name>")
def save_key(profile_id: str, name: str):
    with profile_db(profile_id):
        pass
    if name not in KEY_NAMES:
        abort(404)
    store = _ext()["store"]
    try:
        if request.form.get("action") == "remove":
            store.delete(profile_id, name)
            flash(f"Removed the {KEY_LABELS[name]}.", "ok")
        else:
            store.set(profile_id, name, request.form.get("value", ""))
            flash(f"Saved the {KEY_LABELS[name]}.", "ok")
    except SecretStoreError as exc:
        flash(str(exc), "error")
    return redirect(url_for("web.settings_page", profile_id=profile_id) + "#keys")


@bp.post("/p/<profile_id>/test/<source>")
def test_source(profile_id: str, source: str):
    if source not in runner.SOURCES:
        abort(404)
    kwargs = {}
    factory = _ext()["jobs"].client_factory
    if factory is not None:
        kwargs["client_factory"] = factory
    with profile_db(profile_id) as conn:
        ok, message = runner.check_connection(
            conn, profile_id, source, store=_ext()["store"], **kwargs
        )
    flash(message, "ok" if ok else "error")
    return redirect(url_for("web.settings_page", profile_id=profile_id) + "#keys")


@bp.post("/open-data-folder")
def open_data_folder():
    """Open the data folder in Explorer / Finder. Local convenience only."""
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(folder)  # opens a folder this app controls
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except OSError:
        flash(f"Couldn't open a file browser here. The data folder is {folder}", "error")
    target = request.form.get("next", "")
    # Only same-site paths, so this can't bounce the browser to another site.
    if not target.startswith("/") or target.startswith("//"):
        target = url_for("web.home")
    return redirect(target)


@bp.app_errorhandler(404)
def not_found(error):
    return render_template("error.html", message="That page or profile doesn't exist."), 404


@bp.app_errorhandler(400)
def bad_request(error):
    return render_template("error.html", message=error.description), 400
