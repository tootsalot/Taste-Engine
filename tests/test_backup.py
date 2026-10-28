"""Profile backups use invented data and reject untrusted archives."""

import json
import stat
import zipfile

import pytest

from taste import backup, db, profiles, settings
from taste.config import data_dir
from taste.secrets_store import SecretStore


def test_round_trip_has_rows_recaps_picture_and_no_keys(tmp_path):
    profiles.create("example_user", "Example profile")
    conn = profiles.open_profile("example_user")
    settings.set_value(conn, "timezone", "Asia/Tokyo")
    conn.close()
    folder = data_dir() / "recaps" / "example_user" / "2025"
    folder.mkdir(parents=True)
    (folder / "recap.json").write_text('{"example": true}')
    (folder / "cover.png").write_bytes(b"example picture")
    profiles.picture_path("example_user").write_bytes(b"example avatar")
    SecretStore().set("example_user", "MAL_CLIENT_ID", "invented-key")
    target = tmp_path / "example.tasteprofile"
    backup.create("example_user", target)
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "profile.db",
            "picture.png",
            "recaps/2025/recap.json",
            "recaps/2025/cover.png",
        }
        assert b"invented-key" not in b"".join(archive.read(n) for n in archive.namelist())
    with pytest.raises(profiles.ProfileError, match="new|exists"):
        backup.restore(target)
    restored = backup.restore(target, "restored")
    assert restored.profile_id == "restored"
    with profiles.open_profile("restored") as conn:
        assert settings.get(conn, "timezone") == "Asia/Tokyo"
    assert profiles.picture_path("restored").read_bytes() == b"example avatar"
    assert (data_dir() / "recaps/restored/2025/cover.png").read_bytes() == b"example picture"
    assert SecretStore().get("restored", "MAL_CLIENT_ID") is None


def archive_file(tmp_path, entries, version=1):
    path = tmp_path / "unsafe.tasteprofile"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": version,
                    "profile_id": "restored",
                    "display_name": "Example",
                    "app_version": "2.5.0",
                    "created_at": "2026-01-01 00:00:00",
                }
            ),
        )
        for name, content in entries:
            archive.writestr(name, content)
    return path


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", "recaps/../bad", "recaps\\bad", "keys.json"]
)
def test_rejects_unsafe_members(tmp_path, name):
    with pytest.raises(backup.BackupError):
        backup.restore(archive_file(tmp_path, [(name, b"bad")]))
    assert not profiles.exists("restored")


def test_rejects_newer_formats_and_oversize_and_links(tmp_path, monkeypatch):
    with pytest.raises(backup.BackupError, match="format"):
        backup.restore(archive_file(tmp_path, [("profile.db", b"example")], version=999))
    monkeypatch.setattr(backup, "MAX_UNPACKED_BYTES", 100)
    with pytest.raises(backup.BackupError, match="large|size"):
        backup.restore(archive_file(tmp_path, [("profile.db", b"x" * 101)]))
    monkeypatch.setattr(backup, "MAX_UNPACKED_BYTES", 1 << 30)
    link = zipfile.ZipInfo("recaps/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(backup.BackupError, match="link|regular"):
        backup.restore(archive_file(tmp_path, [(link, b"/outside")]))


def test_rejects_a_backup_from_a_newer_app(tmp_path):
    path = archive_file(tmp_path, [("profile.db", b"example")])
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    manifest["app_version"] = "99.0.0"
    newer = tmp_path / "newer.tasteprofile"
    with zipfile.ZipFile(newer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("profile.db", b"example")
    with pytest.raises(backup.BackupError, match="newer"):
        backup.restore(newer)
    with pytest.raises(backup.BackupError, match="newer"):
        backup.read_manifest(newer)
    assert not profiles.exists("restored")


def test_damaged_database_leaves_no_profile(tmp_path):
    with pytest.raises(backup.BackupError):
        backup.restore(archive_file(tmp_path, [("profile.db", b"not sqlite")]))
    assert not profiles.exists("restored")


def test_retained_recaps_are_not_overwritten(tmp_path):
    profiles.create("example_user")
    target = tmp_path / "example.tasteprofile"
    backup.create("example_user", target)
    retained = data_dir() / "recaps/restored"
    retained.mkdir(parents=True)
    (retained / "kept.json").write_text("kept")
    with pytest.raises(profiles.ProfileError):
        backup.restore(target, "restored")
    assert (retained / "kept.json").read_text() == "kept"


def test_live_wal_and_old_schema(tmp_path):
    profiles.create("example_user")
    conn = profiles.open_profile("example_user")
    settings.set_value(conn, "display_name", "Example live")
    conn.execute("ALTER TABLE stg_mal_anime DROP COLUMN broadcast_time")
    target = tmp_path / "live.tasteprofile"
    backup.create("example_user", target)
    conn.close()
    result = backup.restore(target, "live_copy")
    with db.connect(result.path) as restored:
        assert settings.get(restored, "display_name") == "Example live"
        assert "broadcast_time" in {
            r[1] for r in restored.execute("PRAGMA table_info(stg_mal_anime)")
        }


def test_cli_backup_restore(tmp_path, capsys):
    from taste.__main__ import main

    profiles.create("example_user")
    target = tmp_path / "cli.tasteprofile"
    assert main(["--profile", "example_user", "backup", str(target)]) == 0
    assert main(["restore", str(target), "--as", "cli_copy"]) == 0
    assert profiles.exists("cli_copy")
    assert main(["restore", str(target), "--as", "cli_copy"]) == 2
    assert "exists" in capsys.readouterr().err


def test_desktop_backup_and_restore_with_paths(qtbot, tmp_path):
    from taste.desktop.context import AppContext
    from taste.desktop.main_window import MainWindow

    win = MainWindow(AppContext())
    qtbot.addWidget(win)
    win.new_profile(("example_user", "Example"))
    target = tmp_path / "desktop.tasteprofile"
    assert win.settings.backup_profile(target)
    qtbot.waitUntil(lambda: not win.settings.is_transferring(), timeout=10000)
    qtbot.waitUntil(target.is_file)
    assert win.settings.restore_profile(target, "restored")
    qtbot.waitUntil(lambda: win.current_profile() == "restored", timeout=10000)
    assert win.ctx.threads
    win.close()


def test_restore_offers_the_backups_own_id(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QInputDialog

    from taste.desktop.context import AppContext
    from taste.desktop.main_window import MainWindow

    profiles.create("example_user", "Example")
    target = tmp_path / "moving.tasteprofile"
    backup.create("example_user", target)
    asked = []

    def answer(_parent, _title, prompt, _echo, text):
        asked.append((prompt, text))
        return "", True  # left as it was: keep the backup's own id

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(target), ""))
    monkeypatch.setattr(QInputDialog, "getText", answer)
    # Here the id is taken, so the prompt says so.
    win = MainWindow(AppContext())
    qtbot.addWidget(win)
    assert win.settings.restore_profile()
    qtbot.waitUntil(lambda: not win.settings.is_transferring(), timeout=10000)
    assert asked[-1] == ("That id is already used here. Type a new one.", "example_user")
    win.close()
    # A new computer: the backup's id is offered and kept.
    monkeypatch.setenv("TASTE_DATA_DIR", str(tmp_path / "new-computer"))
    win = MainWindow(AppContext())
    qtbot.addWidget(win)
    assert win.settings.restore_profile()
    qtbot.waitUntil(lambda: win.current_profile() == "example_user", timeout=10000)
    assert asked[-1] == ("Profile id", "example_user")
    win.close()


def test_restore_inside_an_exception_handler_keeps_the_new_profile(tmp_path):
    profiles.create("example_user")
    target = tmp_path / "handled.tasteprofile"
    backup.create("example_user", target)
    try:
        raise ValueError("an unrelated handled exception")
    except ValueError:
        restored = backup.restore(target, "handled")
    assert restored.path.is_file()


def test_a_deeply_nested_manifest_is_a_damaged_backup(tmp_path):
    path = tmp_path / "nested.tasteprofile"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "[" * 10_000 + "]" * 10_000)
        archive.writestr("profile.db", b"example")
    with pytest.raises(backup.BackupError, match="damaged"):
        backup.read_manifest(path)
