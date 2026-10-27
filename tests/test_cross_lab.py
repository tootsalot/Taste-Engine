"""The cross-media check shares matching rules and never changes a profile."""

import sqlite3
import subprocess
import sys
from statistics import mean

import pytest

from taste import __main__ as cli
from taste import activity, db, profiles, settings
from taste.recommend import candidates, cross_lab, everything, lab, selection
from tests.test_lab import library as made_up_library


@pytest.fixture
def cross_library(conn, tmp_path):
    return made_up_library.__wrapped__(conn, tmp_path)


def test_support_bins_and_correlation_have_the_documented_meaning():
    observations = [(0, -1), (0, 1), (0.1, -3), (0.2, -1), (0.3, 0), (0.4, 1), (0.5, 3), (0.6, 5)]
    result = cross_lab._summary("anime", 10, 7, observations)
    assert result["rated"] == 10
    assert result["supported"] == 7
    assert result["checked"] == 8
    assert result["no_support"] == 0
    assert result["low_support"] == -2
    assert result["high_support"] == 4
    assert result["bin_counts"] == {"no_support": 2, "low_support": 2, "high_support": 2}
    assert result["spearman"] == lab.spearman(
        [value for value, _ in observations], [miss for _, miss in observations]
    )
    empty = cross_lab._summary("film", 0, 0, [])
    assert all(
        empty[name] is None for name in ("spearman", "no_support", "low_support", "high_support")
    )


def test_made_up_library_uses_shared_support_and_exact_usual_holdout_without_writes(
    cross_library, monkeypatch
):
    def refuse(*args, **kwargs):
        pytest.fail("Cross lab must not use or write the calibration cache")

    monkeypatch.setattr(selection, "calibration", refuse)
    monkeypatch.setattr(selection, "saved_calibration", refuse)
    before = list(cross_library.iterdump())
    titles = everything.load_cross_data(cross_library)
    index = everything.CrossIndex(titles, hidden=activity.hidden_items(cross_library))
    actual = cross_lab.evaluate(cross_library)
    all_observations = []
    for medium, result in zip(lab.MEDIA, actual, strict=False):
        rated = [title for title in titles if title.medium == medium and title.my_score is not None]
        support = {title.item_key: index.support(title)["cross_support"] for title in rated}
        rows, facts, context = lab.load_data(cross_library, medium)
        predictions = candidates.holdout_predictions(rows, facts, context, candidates.fit_usual)
        observations = [(support[str(key)], mine - guess) for key, mine, guess in predictions]
        expected = cross_lab._summary(
            medium, len(rated), sum(value > 0 for value in support.values()), observations
        )
        assert result == expected
        assert result["checked"] == {"anime": 343, "film": 120, "tv": 30, "book": 130}[medium]
        all_observations.extend(observations)
    overall = actual[-1]
    assert overall["medium"] == "overall"
    assert overall["rated"] == sum(row["rated"] for row in actual[:-1])
    assert overall["supported"] == sum(row["supported"] for row in actual[:-1])
    assert overall["checked"] == 623
    no_support = [miss for support, miss in all_observations if support == 0]
    if no_support:
        assert overall["no_support"] == mean(no_support)
    assert list(cross_library.iterdump()) == before


def test_own_rating_never_changes_its_cross_support(cross_library):
    def supports():
        titles = everything.load_cross_data(cross_library)
        index = everything.CrossIndex(titles, hidden=activity.hidden_items(cross_library))
        return {
            title.item_key: index.support(title)["cross_support"]
            for title in titles
            if title.medium == "film" and title.my_score is not None
        }

    before = supports()
    assert any(value > 0 for value in before.values())
    cross_library.execute(
        "UPDATE core_ratings SET raw_score = scale_min + scale_max - raw_score, "
        "normalized_score = 1 - normalized_score WHERE source = 'tmdb' AND item_id IN "
        "(SELECT item_id FROM core_item_external_ids "
        "WHERE source = 'tmdb' AND id_type = 'tmdb_movie_id')"
    )
    cross_library.execute(
        "UPDATE stg_tmdb_account_ratings SET rating = 10.5 - rating WHERE media_type = 'movie'"
    )
    assert supports() == before


def test_ratings_in_only_one_medium_have_no_cross_support(cross_library):
    cross_library.execute("DELETE FROM core_ratings WHERE source <> 'mal'")
    cross_library.execute("DELETE FROM stg_tmdb_account_ratings")
    cross_library.execute("DELETE FROM stg_hardcover_user_books")
    results = cross_lab.evaluate(cross_library)
    assert results[0]["rated"] == results[0]["checked"] == 343
    assert all(result["supported"] == 0 for result in results)
    assert all(result["spearman"] is None for result in results)
    assert all(result["low_support"] is result["high_support"] is None for result in results)


def test_cross_lab_cli_opens_readonly_and_prints_no_titles_ids_terms(
    cross_library, monkeypatch, capsys
):
    path = cross_library.execute("PRAGMA database_list").fetchone()[2]
    cross_library.commit()
    before = list(cross_library.iterdump())
    title = cross_library.execute("SELECT title FROM core_items LIMIT 1").fetchone()[0]
    settings.set_value(cross_library, "display_name", "Example private display name")
    cross_library.commit()
    before = list(cross_library.iterdump())
    opened = []
    original_connect = sqlite3.connect

    def capture(database, *args, **kwargs):
        opened.append((database, kwargs))
        connection = original_connect(database, *args, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM rec_model_calibrations")
        return connection

    def refuse_setup(*args, **kwargs):
        pytest.fail("The cross lab must not initialize or migrate a database")

    monkeypatch.setattr(sqlite3, "connect", capture)
    monkeypatch.setattr(cli.db, "connect", refuse_setup)
    monkeypatch.setattr(cli.profiles, "migrate_legacy", refuse_setup)
    assert cli.main(["--db", path, "lab", "--cross"]) == 0
    assert len(opened) == 1 and opened[0][1] == {"uri": True}
    assert opened[0][0].endswith("?mode=ro")
    assert list(cross_library.iterdump()) == before
    output = capsys.readouterr().out
    for word in (*lab.MEDIA, "overall", "spearman", "supported", "checked", "doubled stars"):
        assert word in output
    for private in (path, title, "Example private display name", "Time Travel", "Science Fiction"):
        assert private not in output


@pytest.mark.parametrize("arguments", [[], ["--profile", "example_absent"]])
def test_cross_lab_missing_profile_is_not_created(arguments, capsys):
    root = profiles.profiles_dir()
    assert cli.main([*arguments, "lab", "--cross"]) == 2
    assert not root.exists()
    captured = capsys.readouterr()
    assert not captured.out
    assert "does not exist" in captured.err
    assert str(root) not in captured.err


def test_cross_lab_missing_file_and_legacy_database_are_not_created_or_migrated(tmp_path, capsys):
    path = tmp_path / "absent" / "example_private.db"
    assert cli.main(["--db", str(path), "lab", "--cross"]) == 2
    assert not path.parent.exists()
    legacy = profiles.legacy_db()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    db.connect(legacy).close()
    before = legacy.read_bytes()
    assert cli.main(["lab", "--cross"]) == 2
    assert legacy.read_bytes() == before
    assert not profiles.exists("default")
    assert "Moved" not in capsys.readouterr().out


def test_cross_lab_empty_database_has_counts_without_fitting(conn, monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("An empty sample must not fit a model")

    monkeypatch.setattr(candidates, "fit_usual", refuse)
    results = cross_lab.evaluate(conn)
    assert len(results) == 5
    assert all(
        result["rated"] == result["supported"] == result["checked"] == 0 for result in results
    )
    assert all(result["spearman"] is None for result in results)


def test_cross_lab_bad_database_error_has_no_private_path(tmp_path, capsys):
    path = tmp_path / "example_private.db"
    path.write_text("not a database", encoding="utf-8")
    assert cli.main(["--db", str(path), "lab", "--cross"]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "The lab could not read the profile database." in captured.err
    assert str(path) not in captured.err


def test_cross_lab_import_is_qt_free():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import taste.recommend.cross_lab; "
            "assert not any(name.startswith('PySide6') for name in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
