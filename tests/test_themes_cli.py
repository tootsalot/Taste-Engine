"""Theme coverage prints taxonomy counts from an existing read-only database."""

import sqlite3
import subprocess
import sys

import pytest

from taste import __main__ as cli
from taste import core, db, profiles, settings, themes
from tests.test_lab_cli import row_counts


def make_database(path):
    conn = db.connect(path)
    settings.set_value(conn, "display_name", "Example private display name")
    for index, tags in enumerate((("Action", "Example unmapped genre"), ("Example odd genre",))):
        item = core.upsert_item(
            conn,
            source="mal",
            id_type="mal_anime_id",
            external_id=str(981000 + index),
            media_type="anime",
            title=f"Example private title {index}",
        )
        core.upsert_rating(
            conn,
            item_id=item,
            source="mal",
            raw_score=8,
            scale_min=1,
            scale_max=10,
            source_updated_at=None,
        )
        core.set_item_tags(conn, item, "mal", [core.tag_id(conn, tag, "genre") for tag in tags])
        # Creator and arbitrary metadata labels are not taxonomy terms to print.
        creator = core.upsert_creator(
            conn,
            source="mal",
            id_type="studio_id",
            external_id=str(index),
            name="Example private person",
        )
        core.link_item_creator(conn, item, creator, "studio", "mal")
    return conn


def test_themes_opens_readonly_uri_without_setup_or_writes(tmp_path, monkeypatch, capsys):
    path = tmp_path / "database # with spaces.db"
    conn = make_database(path)
    before_counts, before_dump = row_counts(conn), list(conn.iterdump())
    conn.close()
    original_connect = sqlite3.connect
    opened = []

    def capture(database, *args, **kwargs):
        opened.append((database, kwargs))
        connection = original_connect(database, *args, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM core_tags")
        return connection

    def refuse_setup(*args, **kwargs):
        raise AssertionError("Theme coverage must not initialize or migrate a database")

    monkeypatch.setattr(sqlite3, "connect", capture)
    monkeypatch.setattr(cli.db, "connect", refuse_setup)
    monkeypatch.setattr(cli.profiles, "migrate_legacy", refuse_setup)
    assert cli.main(["--db", str(path), "themes"]) == 0
    assert opened == [(f"{path.resolve().as_uri()}?mode=ro", {"uri": True})]
    with original_connect(path) as check:
        assert row_counts(check) == before_counts
        assert list(check.iterdump()) == before_dump
    out = capsys.readouterr().out
    assert "anime: 1/2 rated titles mapped; 1/3 tags mapped" in out
    assert "example unmapped genre" in out.lower()
    for private in (str(path), "Example private", "981000"):
        assert private not in out


@pytest.mark.parametrize("arguments", [[], ["--profile", "example_absent"]])
def test_themes_refuses_missing_profiles_without_creating_them(arguments, capsys):
    root = profiles.profiles_dir()
    assert not root.exists()
    assert cli.main([*arguments, "themes"]) == 2
    assert not root.exists()
    captured = capsys.readouterr()
    assert not captured.out
    assert "does not exist" in captured.err
    assert "example_absent" not in captured.err
    assert str(root) not in captured.err


def test_themes_refuses_missing_file_without_creating_parent(tmp_path, capsys):
    path = tmp_path / "missing" / "private.db"
    assert cli.main(["--db", str(path), "themes"]) == 2
    assert not path.parent.exists()
    assert str(path) not in capsys.readouterr().err


def test_themes_never_migrates_a_legacy_database(capsys):
    path = profiles.legacy_db()
    path.parent.mkdir(parents=True)
    db.connect(path).close()
    before = path.read_bytes()
    assert cli.main(["themes"]) == 2
    assert path.read_bytes() == before
    assert not profiles.exists("default")
    assert "Moved" not in capsys.readouterr().out


def test_themes_named_profile_prints_only_taxonomy_counts(capsys):
    path = profiles.db_path("example_private")
    make_database(path).close()
    assert cli.main(["--profile", "example_private", "themes"]) == 0
    out = capsys.readouterr().out
    for medium in ("anime", "film", "tv", "book", "music"):
        assert medium in out.lower()
    for private in ("Example private", "example_private", str(path), "981000"):
        assert private not in out
    assert "recorded plays" in out
    assert "capture" in out
    assert "not ratings" in out


def test_themes_loads_configured_data_directory(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "configured-data"
    path = folder / "profiles" / "example_configured.db"
    make_database(path).close()
    loaded = []

    def load_test_env():
        loaded.append(True)
        monkeypatch.setenv("TASTE_DATA_DIR", str(folder))

    monkeypatch.setattr(cli, "load_env", load_test_env)
    assert cli.main(["--profile", "example_configured", "themes"]) == 0
    assert loaded == [True]
    assert "example_configured" not in capsys.readouterr().out


def test_themes_limits_unmapped_terms_and_removes_terminal_controls(conn, monkeypatch, capsys):
    counts = {"titles": 1, "mapped_titles": 1, "tags": 2, "mapped_tags": 1}
    result = {
        "media": {medium: counts for medium in ("anime", "film", "tv", "book", "music")},
        "unmapped": [(f"example genre {index}", 30 - index) for index in range(21)],
    }
    result["unmapped"][0] = ("example\ncontrol\x1b\tgenre", 30)
    monkeypatch.setattr(themes, "coverage", lambda _: result)
    assert cli.cmd_themes(conn) == 0
    out = capsys.readouterr().out
    terms = out.split("Most common unmapped genre/tag terms (up to 20):\n")[1].splitlines()
    assert len(terms) == 20
    assert "example control genre" in terms[0]
    assert "example genre 20" not in out
    assert "\x1b" not in out


@pytest.mark.parametrize("contents", [b"not a database", b""])
def test_themes_bad_database_has_generic_error(tmp_path, capsys, contents):
    path = tmp_path / "example_private.db"
    path.write_bytes(contents)
    assert cli.main(["--db", str(path), "themes"]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "could not read" in captured.err
    assert str(path) not in captured.err
    assert "example_private" not in captured.err


def test_themes_import_does_not_load_qt():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import taste.__main__; "
            "assert not any(name.startswith('PySide6') for name in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
