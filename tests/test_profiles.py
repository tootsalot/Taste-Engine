import pytest

from taste import config, desktop, profiles, settings
from taste.profiles import ProfileError
from taste.secrets_store import SecretStore
from tests.conftest import FakeKeyring


@pytest.mark.parametrize(
    "bad", ["", "..", "../x", "a/b", "a\\b", "UPPER", "-start", "x" * 41, "has space", "dot.db"]
)
def test_rejects_unsafe_ids(bad):
    with pytest.raises(ProfileError):
        profiles.validate_id(bad)


@pytest.mark.parametrize("good", ["default", "me", "friend-2", "a_b", "0", "x" * 40])
def test_accepts_simple_ids(good):
    assert profiles.validate_id(good) == good


def test_create_list_and_names():
    profiles.create("me", "Me")
    profiles.create("friend")
    found = profiles.list_profiles()
    assert [(p.profile_id, p.display_name) for p in found] == [("friend", "friend"), ("me", "Me")]
    assert all(p.path.parent == profiles.profiles_dir() for p in found)
    with pytest.raises(ProfileError, match="already exists"):
        profiles.create("me")


def test_profiles_are_separate_databases():
    profiles.create("a")
    profiles.create("b")
    conn_a = profiles.open_profile("a")
    settings.set_value(conn_a, "timezone", "Europe/London")
    conn_a.close()
    conn_b = profiles.open_profile("b")
    assert settings.get(conn_b, "timezone") == "America/Phoenix"
    conn_b.close()


def test_delete_removes_database_and_keys():
    profiles.create("gone")
    store = SecretStore()
    store.set("gone", "LASTFM_API_KEY", "fake-key")
    profiles.delete("gone")
    assert not profiles.exists("gone")
    assert not (profiles.profiles_dir() / "gone.keys.json").exists()
    with pytest.raises(ProfileError):
        profiles.delete("gone")


def test_open_missing_profile_fails():
    with pytest.raises(ProfileError, match="No profile"):
        profiles.open_profile("nobody")


def test_migration_only_runs_once():
    legacy = profiles.legacy_db()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"")
    assert profiles.migrate_legacy() is True
    legacy.write_bytes(b"")  # a second legacy file must not overwrite the profile
    assert profiles.migrate_legacy() is False
    assert legacy.exists()


def test_uninstall_cleanup_deletes_keys_and_the_whole_data_folder():
    ring = FakeKeyring()
    store = SecretStore(backend=ring)
    for pid in ("me", "friend"):
        profiles.create(pid)
        store.set(pid, "MAL_CLIENT_ID", f"fake-id-{pid}")
    folder = config.data_dir()
    (folder / "cache" / "images").mkdir(parents=True)
    (folder / "ui_state.json").write_text("{}", encoding="utf-8")

    assert profiles.delete_all_data(store) == folder
    assert not folder.exists()
    assert ring.saved == {}  # no keys left behind in the credential store


def test_uninstall_cleanup_leaves_files_it_did_not_make(monkeypatch, tmp_path):
    shared = tmp_path / "shared"  # say TASTE_DATA_DIR pointed at a folder used for more
    monkeypatch.setenv("TASTE_DATA_DIR", str(shared))
    profiles.create("me")
    (shared / "cache").mkdir()
    (shared / "my-notes.txt").write_text("not the app's", encoding="utf-8")
    (shared / "photos").mkdir()

    assert profiles.delete_all_data(SecretStore(backend=FakeKeyring())) == shared
    assert sorted(p.name for p in shared.iterdir()) == ["my-notes.txt", "photos"]


def test_uninstall_cleanup_refuses_to_delete_a_source_checkout(monkeypatch):
    monkeypatch.delenv("TASTE_DATA_DIR")  # from source, data/ is the project's own folder
    monkeypatch.setattr(profiles.shutil, "rmtree", lambda *a, **k: pytest.fail("deleted"))
    assert profiles.delete_all_data(SecretStore(backend=FakeKeyring())) is None


def test_the_app_flag_runs_the_cleanup_without_a_window(monkeypatch):
    profiles.create("me")
    folder = config.data_dir()
    assert desktop.main(["taste-engine", "--delete-all-data"]) == 0
    assert not folder.exists()
    monkeypatch.delenv("TASTE_DATA_DIR")
    assert desktop.main(["taste-engine", "--delete-all-data"]) == 1  # refused from source
