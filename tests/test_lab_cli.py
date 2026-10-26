"""The lab CLI reads existing profiles without exposing their contents."""

import re
import sqlite3
import subprocess
import sys

import pytest

from taste import __main__ as cli
from taste import db, profiles, settings
from taste.sync_log import SyncRun


def make_database(path, count=25):
    conn = db.connect(path)
    settings.set_value(conn, "display_name", "Example private display name")
    run = SyncRun(conn, "mal", "full")
    for offset in range(count):
        key = 981000 + offset
        conn.execute(
            "INSERT INTO stg_mal_anime "
            "(mal_anime_id, title, community_mean, nsfw_rating, loaded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, f"Example private title {offset}", 6 + offset % 4, "black", "2026-10-14"),
        )
        conn.execute(
            "INSERT INTO stg_mal_list_entries (mal_anime_id, status, score, "
            "num_episodes_watched, is_rewatching, mal_updated_at, first_seen_at, "
            "last_seen_run_id) VALUES (?, 'completed', ?, 12, 0, ?, ?, ?)",
            (key, 4 + offset % 7, "2026-10-14", "2026-10-14", run.run_id),
        )
    run.finish("success")
    return conn


def row_counts(conn):
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {
        row[0]: conn.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0] for row in tables
    }


def test_lab_opens_a_readonly_uri_without_setup_and_writes_nothing(tmp_path, monkeypatch, capsys):
    path = tmp_path / "database # with spaces.db"
    conn = make_database(path)
    before_counts = row_counts(conn)
    before_dump = list(conn.iterdump())
    conn.close()
    opened = []
    original_connect = sqlite3.connect

    def capture(database, *args, **kwargs):
        opened.append((database, kwargs))
        connection = original_connect(database, *args, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM rec_runs")
        return connection

    def refuse_setup(*args, **kwargs):
        raise AssertionError("The lab must not initialize a database or migrate profiles")

    monkeypatch.setattr(sqlite3, "connect", capture)
    monkeypatch.setattr(cli.db, "connect", refuse_setup)
    monkeypatch.setattr(cli.profiles, "migrate_legacy", refuse_setup)
    assert cli.main(["--db", str(path), "lab", "--medium", "anime"]) == 0
    out = capsys.readouterr().out
    assert len(opened) == 1
    assert opened[0][0] == f"{path.resolve().as_uri()}?mode=ro"
    assert opened[0][1]["uri"] is True
    with original_connect(path) as check:
        assert row_counts(check) == before_counts
        assert list(check.iterdump()) == before_dump
    for private in (str(path), "Example private", "981000"):
        assert private not in out
    assert "whole_mae" in out
    assert "25" in out
    for candidate in ("usual", "dismissals", "recency"):
        assert candidate in out


@pytest.mark.parametrize("arguments", [[], ["--profile", "example_absent"]])
def test_lab_refuses_missing_profiles_without_creating_them(arguments, capsys):
    root = profiles.profiles_dir()
    assert not root.exists()
    assert cli.main([*arguments, "lab"]) == 2
    assert not root.exists()
    captured = capsys.readouterr()
    assert not captured.out
    assert "does not exist" in captured.err
    assert "example_absent" not in captured.err
    assert str(root) not in captured.err


def test_lab_refuses_missing_database_without_creating_a_directory(tmp_path, capsys):
    path = tmp_path / "absent" / "private.db"
    assert cli.main(["--db", str(path), "lab"]) == 2
    assert not path.parent.exists()
    assert str(path) not in capsys.readouterr().err


def test_lab_never_migrates_the_legacy_database(capsys):
    path = profiles.legacy_db()
    path.parent.mkdir(parents=True)
    db.connect(path).close()
    before = path.read_bytes()
    assert cli.main(["lab"]) == 2
    assert path.read_bytes() == before
    assert not profiles.exists("default")
    assert "Moved" not in capsys.readouterr().out


def test_lab_named_profile_prints_only_metrics(capsys):
    path = profiles.db_path("example_private")
    make_database(path).close()
    assert cli.main(["--profile", "example_private", "lab", "--medium", "all"]) == 0
    out = capsys.readouterr().out
    for medium in ("anime", "film", "tv", "book"):
        assert medium in out.lower()
    assert "doubled" in out.lower()
    assert "Example private" not in out
    assert "example_private" not in out
    assert str(path) not in out
    assert "watched" not in out.lower()


def test_lab_loads_the_configured_data_directory(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "configured-data"
    path = folder / "profiles" / "example_configured.db"
    make_database(path).close()
    loaded = []

    def load_test_env():
        loaded.append(True)
        monkeypatch.setenv("TASTE_DATA_DIR", str(folder))

    monkeypatch.setattr(cli, "load_env", load_test_env)
    assert cli.main(["--profile", "example_configured", "lab", "--medium", "anime"]) == 0
    assert loaded == [True]
    out = capsys.readouterr().out
    assert "25" in out
    assert "example_configured" not in out
    assert str(folder) not in out


def test_lab_under_minimum_prints_only_its_count(tmp_path, capsys):
    path = tmp_path / "small.db"
    make_database(path, count=19).close()
    assert cli.main(["--db", str(path), "lab", "--medium", "anime"]) == 0
    out = capsys.readouterr().out
    for line in out.splitlines():
        if line.lstrip().startswith(("usual", "dismissals", "recency")):
            assert re.findall(r"\d+(?:\.\d+)?", line) == ["19"]
            assert "n/a" in line


def test_lab_bad_database_has_a_plain_error_without_its_path(tmp_path, capsys):
    path = tmp_path / "example_private.db"
    path.write_text("not a database", encoding="utf-8")
    assert cli.main(["--db", str(path), "lab"]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert str(path) not in captured.err
    assert "example_private" not in captured.err


def test_lab_import_does_not_load_qt():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import taste.recommend.lab; "
            "assert not any(name.startswith('PySide6') for name in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
