import os
import sqlite3
import stat

import pytest

from taste import credentials, profiles
from taste.config import ConfigError
from taste.secrets_store import SecretStore, SecretStoreError
from tests.conftest import FakeKeyring

KEY = "fake-lastfm-key-for-tests"


def test_keyring_backend_stores_outside_the_database():
    profiles.create("me")
    ring = FakeKeyring()
    store = SecretStore(backend=ring)
    assert store.secure
    store.set("me", "LASTFM_API_KEY", f"  {KEY}  ")
    assert ring.saved == {("taste-engine", "me:LASTFM_API_KEY"): KEY}
    assert store.get_with_source("me", "LASTFM_API_KEY") == (KEY, "keyring")
    assert not (profiles.profiles_dir() / "me.keys.json").exists()
    db_bytes = profiles.db_path("me").read_bytes()
    assert KEY.encode() not in db_bytes
    store.delete("me", "LASTFM_API_KEY")
    assert ring.saved == {}


def test_file_fallback_when_no_keyring():
    store = SecretStore(backend=None)
    assert not store.secure
    store.set("me", "MAL_CLIENT_ID", "fake-mal-id")
    path = profiles.profiles_dir() / "me.keys.json"
    assert path.exists()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.get_with_source("me", "MAL_CLIENT_ID") == ("fake-mal-id", "file")
    store.delete("me", "MAL_CLIENT_ID")
    assert not path.exists()  # last key gone, file gone


def test_environment_fallback(monkeypatch):
    store = SecretStore(backend=FakeKeyring())
    assert store.get_with_source("me", "MAL_CLIENT_ID") == (None, None)
    monkeypatch.setenv("MAL_CLIENT_ID", "from-env")
    assert store.get_with_source("me", "MAL_CLIENT_ID") == ("from-env", "env")
    store.set("me", "MAL_CLIENT_ID", "stored")
    assert store.get("me", "MAL_CLIENT_ID") == "stored"  # stored key wins over env


def test_rejects_bad_input():
    store = SecretStore(backend=FakeKeyring())
    with pytest.raises(SecretStoreError, match="empty"):
        store.set("me", "MAL_CLIENT_ID", "   ")
    with pytest.raises(SecretStoreError, match="Unknown key"):
        store.set("me", "PASSWORD", "x")
    with pytest.raises(profiles.ProfileError):
        store.set("../evil", "MAL_CLIENT_ID", "x")


def test_backend_errors_never_include_the_key():
    class Broken(FakeKeyring):
        def set_password(self, service, entry, value):
            raise RuntimeError(f"failed to save {value}")

    store = SecretStore(backend=Broken())
    with pytest.raises(SecretStoreError) as info:
        store.set("me", "LASTFM_API_KEY", KEY)
    assert KEY not in str(info.value)


def test_credentials_use_settings_and_store(tmp_path):
    from taste import db, settings

    conn = db.connect(tmp_path / "p.db")
    store = SecretStore(backend=FakeKeyring())
    with pytest.raises(ConfigError, match="Last.fm API key, username"):
        credentials.lastfm(conn, "me", store)
    store.set("me", "LASTFM_API_KEY", KEY)
    settings.set_value(conn, "lastfm_username", "example_user")
    cfg = credentials.lastfm(conn, "me", store)
    assert (cfg.api_key, cfg.username) == (KEY, "example_user")
    # Keys never end up in the settings table.
    rows = conn.execute("SELECT setting_value FROM app_settings").fetchall()
    assert all(KEY not in r[0] for r in rows)
    assert isinstance(conn, sqlite3.Connection)
