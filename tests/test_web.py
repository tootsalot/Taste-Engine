import io
import time
import zipfile

import pytest

from taste import profiles
from taste.http_client import HttpClient
from taste.secrets_store import SecretStore
from taste.web import create_app
from taste.web.jobs import Job
from tests.conftest import FakeKeyring, FakeResponse, FakeTransport, load_fixture
from tests.test_lastfm_sync import FakeLastfm

MAL_KEY = "fake-mal-client-id-web"
LASTFM_KEY = "fake-lastfm-key-web-0123"
BASE = "http://127.0.0.1:8765"


def api_handler():
    lastfm = FakeLastfm("lastfm_recent_page1.json", "lastfm_recent_page2.json")

    def handler(url, params):
        if "myanimelist" in url:
            name = "mal_animelist_page2.json" if "offset=3" in url else "mal_animelist_page1.json"
            return FakeResponse(200, load_fixture(name))
        if params.get("method") == "user.getinfo":
            if params.get("api_key") != LASTFM_KEY:
                return FakeResponse(403, {"error": 10, "message": "Invalid API key"})
            return FakeResponse(200, {"user": {"name": params["user"]}})
        return lastfm(url, params)

    return handler


@pytest.fixture
def app():
    handler = api_handler()

    def client_factory(secrets):
        return HttpClient(
            FakeTransport(handler), secrets=secrets, sleep=lambda s: None, jitter=lambda: 0.0
        )

    app = create_app(
        store=SecretStore(backend=FakeKeyring()),
        client_factory=client_factory,
        sync_kwargs={"now": lambda: 1791500000},
        secret_key="test-secret",
    )
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    c = app.test_client()
    with c.session_transaction(base_url=BASE) as s:
        s["csrf"] = "tok"
    return c


def post(client, path, **data):
    return client.post(path, base_url=BASE, data={"csrf_token": "tok", **data})


def get(client, path):
    return client.get(path, base_url=BASE)


def make_profile(client, pid="me"):
    resp = post(client, "/profiles", profile_id=pid, display_name="Me")
    assert resp.status_code == 302
    return pid


def configure(client, pid):
    post(client, f"/p/{pid}/keys/MAL_CLIENT_ID", value=MAL_KEY, action="save")
    post(client, f"/p/{pid}/keys/LASTFM_API_KEY", value=LASTFM_KEY, action="save")
    settings_form = {
        "display_name": "Me",
        "mal_username": "example_user",
        "lastfm_username": "example_user",
        "timezone": "America/Phoenix",
        "lastfm_capture_scope": "desktop_only",
        "lastfm_scope_note": "",
        "include_nsfw": "on",
        "genre_min_sample": "1",
        "in_line_threshold": "0.25",
        "top_n_all_time": "50",
        "top_n_per_year": "25",
        "top_n_per_month": "10",
        "lastfm_lookback_days": "14",
    }
    assert post(client, f"/p/{pid}/settings", **settings_form).status_code == 302


def wait_for_sync(app, pid, timeout=10):
    jobs = app.extensions["taste"]["jobs"]
    deadline = time.time() + timeout
    while jobs.is_running(pid):
        assert time.time() < deadline, "sync didn't finish"
        time.sleep(0.05)
    return jobs.get(pid)


def test_home_and_create_profile(client):
    assert b"No profiles yet" in get(client, "/").data
    resp = post(client, "/profiles", profile_id="me", display_name="Me")
    assert resp.headers["Location"].endswith("/p/me/settings")
    assert profiles.exists("me")
    page = get(client, "/").data
    assert b"Me" in page and b"<code>me</code>" in page


def test_bad_profile_id_is_rejected(client):
    resp = post(client, "/profiles", profile_id="../evil")
    assert resp.status_code == 302
    assert not list(profiles.profiles_dir().glob("*.db"))
    assert b"Profile IDs use lowercase" in get(client, "/").data


def test_post_without_csrf_token_is_rejected(client):
    resp = client.post("/profiles", base_url=BASE, data={"profile_id": "me"})
    assert resp.status_code == 400
    assert not profiles.exists("me")


def test_foreign_host_is_rejected(client):
    assert client.get("/", base_url="http://evil.example").status_code == 400
    assert client.get("/", base_url="http://localhost:8765").status_code == 200


def test_unknown_and_unsafe_profiles_404(client):
    assert get(client, "/p/nobody/").status_code == 404
    assert get(client, "/p/..%2F..%2Fetc/").status_code == 404
    assert get(client, "/p/UPPER/settings").status_code == 404


def test_security_headers(client):
    resp = get(client, "/")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]


def test_settings_save_and_validation(client):
    pid = make_profile(client)
    post(client, f"/p/{pid}/settings", genre_min_sample="0", timezone="America/Denver")
    page = get(client, f"/p/{pid}/settings").data
    assert b"must be at least 1" in page
    assert b'value="America/Denver"' in page  # valid fields still saved
    # An unchecked checkbox is simply missing from the form, which means off.
    post(client, f"/p/{pid}/settings", timezone="America/Denver")
    conn = profiles.open_profile(pid)
    from taste import settings

    assert settings.get(conn, "include_nsfw") is False
    conn.close()


def test_keys_are_saved_but_never_shown(app, client):
    pid = make_profile(client)
    configure(client, pid)
    ring = app.extensions["taste"]["store"].backend
    assert ring.saved[("taste-engine", f"{pid}:LASTFM_API_KEY")] == LASTFM_KEY
    for path in ("/", f"/p/{pid}/", f"/p/{pid}/settings", f"/p/{pid}/reports", f"/p/{pid}/sync"):
        body = get(client, path).data
        assert LASTFM_KEY.encode() not in body and MAL_KEY.encode() not in body, path
    assert b"Saved in the system credential store" in get(client, f"/p/{pid}/settings").data
    post(client, f"/p/{pid}/keys/LASTFM_API_KEY", action="remove")
    assert ("taste-engine", f"{pid}:LASTFM_API_KEY") not in ring.saved


def test_test_connection(client):
    pid = make_profile(client)
    post(client, f"/p/{pid}/test/lastfm")
    assert b"Last.fm needs: Last.fm API key, username" in get(client, f"/p/{pid}/settings").data
    configure(client, pid)
    post(client, f"/p/{pid}/test/lastfm")
    assert b"connected as example_user" in get(client, f"/p/{pid}/settings").data
    post(client, f"/p/{pid}/keys/LASTFM_API_KEY", value="wrong-key", action="save")
    post(client, f"/p/{pid}/test/lastfm")
    page = get(client, f"/p/{pid}/settings").data
    assert b"Invalid API key" in page
    assert b"wrong-key" not in page


def test_sync_reports_and_downloads(app, client):
    pid = make_profile(client)
    configure(client, pid)
    resp = post(client, f"/p/{pid}/sync", source="all")
    assert resp.headers["Location"].endswith(f"/p/{pid}/sync")
    job = wait_for_sync(app, pid)
    assert job.results == {"mal": True, "lastfm": True}

    status = get(client, f"/p/{pid}/sync/status").get_json()
    assert status["ok"] is True
    assert any("Last.fm: page 1 of 1" in m for m in status["messages"])

    dash = get(client, f"/p/{pid}/").data
    assert b"Last.fm plays" in dash

    page = get(client, f"/p/{pid}/reports").data
    assert b"3 scored shows" in page
    assert b"Desktop listening only" in page
    assert b"Top artists, all time" in page

    csv_resp = get(client, f"/p/{pid}/reports/lastfm_by_hour.csv")
    text = csv_resp.data.decode("utf-8")
    assert text.startswith("﻿local_hour,plays,pct_of_plays,data_scope,capture_scopes")
    assert get(client, f"/p/{pid}/reports/nope.csv").status_code == 404

    archive = zipfile.ZipFile(io.BytesIO(get(client, f"/p/{pid}/reports.zip").data))
    assert len(archive.namelist()) == 12


def test_second_sync_is_refused_while_running(app, client):
    pid = make_profile(client)
    jobs = app.extensions["taste"]["jobs"]
    jobs._jobs[pid] = Job(pid, "all", "2026-01-01 00:00:00")  # still marked running
    post(client, f"/p/{pid}/sync", source="all")
    assert b"already running" in get(client, f"/p/{pid}/sync").data


def test_delete_needs_typed_confirmation(app, client):
    pid = make_profile(client)
    configure(client, pid)
    post(client, f"/p/{pid}/delete", confirm="wrong")
    assert profiles.exists(pid)
    post(client, f"/p/{pid}/delete", confirm=pid)
    assert not profiles.exists(pid)
    assert app.extensions["taste"]["store"].backend.saved == {}
    assert get(client, f"/p/{pid}/").status_code == 404
