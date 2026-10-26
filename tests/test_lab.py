"""Rating models share held-out rows, without touching a real profile or API."""

import math
import random
import statistics
from datetime import date

import pytest

from taste import credentials, recommend, runner, settings
from taste.recommend import experiments, lab
from taste.recommend.anime import _anime_facts, _scored
from taste.recommend.books import _book_facts, _book_scored
from taste.recommend.model import build_model, holdout_residuals, whole_score
from taste.recommend.screen import _screen_facts, _screen_scored, _typical_tmdb_average
from taste.secrets_store import SecretStore
from taste.sources.hardcover_auth import Tokens
from tests import fake_hardcover, fake_tmdb
from tests.conftest import FakeKeyring
from tests.fill_world import NOW, NOW_DT, World


@pytest.fixture
def library(conn, tmp_path):
    world = World()
    store = SecretStore(tmp_path, backend=FakeKeyring())
    pid = "example_user"
    settings.set_value(conn, "mal_username", pid)
    store.set(pid, "MAL_CLIENT_ID", "fake-mal-client-id")
    store.set(pid, "TMDB_READ_TOKEN", fake_tmdb.READ_TOKEN)
    store.set(pid, "TMDB_USER_TOKEN", fake_tmdb.USER_TOKEN)
    store.set(pid, "TMDB_ACCOUNT_ID", fake_tmdb.ACCOUNT)
    credentials.save_hardcover(
        pid,
        store,
        Tokens(fake_hardcover.ACCESS, fake_hardcover.REFRESH, "2099-01-01 00:00:00"),
    )
    for source in ("mal", "tmdb", "hardcover"):
        assert runner.sync_sources(
            conn,
            pid,
            source,
            store=store,
            client_factory=world.client_factory,
            out=lambda _: None,
            now=lambda: NOW,
        ) == {source: True}
    return conn


def own_rows(conn, medium):
    if medium == "anime":
        return _scored(conn), _anime_facts(conn)
    if medium == "book":
        return _book_scored(conn), _book_facts(conn)
    kind = "movie" if medium == "film" else "tv"
    return (
        _screen_scored(
            conn, _typical_tmdb_average(conn, kind), settings.get(conn, "film_prior_votes"), kind
        ),
        _screen_facts(conn, kind),
    )


@pytest.mark.parametrize("medium,count", [("anime", 343), ("film", 120), ("tv", 30), ("book", 130)])
def test_library_metrics_and_usual_residuals_match_the_existing_model(library, medium, count):
    rows, facts = own_rows(library, medium)
    loaded_rows, loaded_facts, context = lab.load_data(library, medium)
    assert loaded_rows == rows
    assert loaded_facts == facts
    held_out = lab.holdout_predictions(rows, facts, context, lab.DEFAULT_CANDIDATES[0])
    assert [mine - guess for _, mine, guess in held_out] == holdout_residuals(rows, facts)
    assert sorted(key for key, _, _ in held_out) == sorted(key for key, _, _ in rows)
    results = lab.evaluate(library, medium, lab.DEFAULT_CANDIDATES)
    assert [r["candidate"] for r in results] == ["usual", "dismissals", "recency"]
    for result, candidate in zip(results, lab.DEFAULT_CANDIDATES, strict=True):
        predictions = lab.holdout_predictions(rows, facts, context, candidate)
        errors = [abs(mine - guess) for _, mine, guess in predictions]
        assert result["count"] == count == len(predictions)
        assert result["mae"] == statistics.mean(errors)
        assert -1 <= result["spearman"] <= 1
        assert 0 <= result["top_hits"] <= 1
        assert result["seconds"] >= 0
        assert result["verdict"] in ("better", "same", "worse")
        assert result["vs_usual"] == pytest.approx(result["mae"] - results[0]["mae"])
        if medium == "anime":
            assert result["whole_mae"] == statistics.mean(
                abs(mine - whole_score(guess)) for _, mine, guess in predictions
            )
        else:
            assert result["whole_mae"] is None
    assert results[0]["vs_usual"] == 0
    assert results[0]["interval"] == (0, 0)
    assert results[0]["verdict"] == "same"
    if medium == "book":
        assert all(1 <= mine <= 10 and 1 <= crowd <= 10 for _, mine, crowd in rows)


@pytest.mark.parametrize("medium", lab.MEDIA)
@pytest.mark.parametrize("candidate", lab.DEFAULT_CANDIDATES, ids=lambda c: c.name)
def test_changing_a_folds_held_out_ratings_never_changes_its_predictions(
    library, medium, candidate
):
    rows, facts, context = lab.load_data(library, medium)
    before = lab.holdout_predictions(rows, facts, context, candidate)
    for fold in range(5):
        changed = [
            (key, 11 - mine if key % 5 == fold else mine, crowd) for key, mine, crowd in rows
        ]
        after = lab.holdout_predictions(changed, facts, context, candidate)
        assert [(key, guess) for key, _, guess in before if key % 5 == fold] == [
            (key, guess) for key, _, guess in after if key % 5 == fold
        ]


@pytest.mark.parametrize("medium", lab.MEDIA)
def test_held_out_database_scores_cannot_leak_through_context(library, medium):
    rows, facts, context = lab.load_data(library, medium)
    before = [lab.holdout_predictions(rows, facts, context, c) for c in lab.DEFAULT_CANDIDATES]
    source, id_type, table, column, rating = {
        "anime": ("mal", "mal_anime_id", "stg_mal_list_entries", "mal_anime_id", "score"),
        "film": ("tmdb", "tmdb_movie_id", "stg_tmdb_account_ratings", "tmdb_id", "rating"),
        "tv": ("tmdb", "tmdb_tv_id", "stg_tmdb_account_ratings", "tmdb_id", "rating"),
        "book": ("hardcover", "hardcover_book_id", "stg_hardcover_user_books", "book_id", "rating"),
    }[medium]
    suffix = " AND media_type = ?" if medium in ("film", "tv") else ""
    params = ("movie" if medium == "film" else "tv",) if suffix else ()
    for fold in range(5):
        library.execute("SAVEPOINT held_out_scores")
        library.execute(
            f"UPDATE {table} SET {rating} = ? - {rating} WHERE {column} % 5 = ?" + suffix,
            (5.5 if medium == "book" else 11, fold, *params),
        )
        library.execute(
            "UPDATE core_ratings SET raw_score = scale_min + scale_max - raw_score, "
            "normalized_score = 1 - normalized_score WHERE source = ? AND item_id IN "
            "(SELECT item_id FROM core_item_external_ids WHERE source = ? AND id_type = ? "
            "AND CAST(external_id AS INTEGER) % 5 = ?)",
            (source, source, id_type, fold),
        )
        changed_rows, changed_facts, changed_context = lab.load_data(library, medium)
        assert changed_rows != rows
        assert changed_facts == facts
        assert changed_context == context
        for candidate, expected in zip(lab.DEFAULT_CANDIDATES, before, strict=True):
            after = lab.holdout_predictions(changed_rows, changed_facts, changed_context, candidate)
            assert [(key, guess) for key, _, guess in after if key % 5 == fold] == [
                (key, guess) for key, _, guess in expected if key % 5 == fold
            ]
        library.execute("ROLLBACK TO held_out_scores")
        library.execute("RELEASE held_out_scores")


def test_rank_metrics_handle_ties_and_constant_predictions():
    assert lab.spearman([1, 1, 3, 4], [4, 2, 2, 1]) == pytest.approx(-5 / 6)
    assert lab.spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1)
    assert lab.spearman([1, 1, 1], [1, 2, 3]) is None
    assert lab.spearman([1, 2, 3], [1, 1, 1]) is None
    # ceil(6 / 5) = 2; one of the two highest-rated titles is predicted in the top two.
    predictions = [(9, 10, 3), (2, 10, 9), (3, 7, 9), (4, 6, 6), (5, 5, 5), (6, 4, 4)]
    assert lab.top_hits(predictions) == 0.5
    assert lab.top_hits(list(reversed(predictions))) == 0.5


def test_paired_bootstrap_uses_error_differences_and_is_repeatable():
    usual = [2.0, 0.5, 3.0, 1.0] * 5
    errors = [1.0, 1.5, 2.0, 0.0] * 5
    difference, interval = lab.paired_bootstrap(errors, usual)
    deltas = [a - b for a, b in zip(errors, usual, strict=True)]
    rng = random.Random(0)
    samples = sorted(statistics.mean(rng.choices(deltas, k=len(deltas))) for _ in range(1000))
    # Independently interpolate the empirical 2.5th and 97.5th percentiles.
    expected = []
    for fraction in (0.025, 0.975):
        position = (len(samples) - 1) * fraction
        low = math.floor(position)
        expected.append(samples[low] + (samples[low + 1] - samples[low]) * (position - low))
    assert difference == statistics.mean(deltas)
    assert interval == pytest.approx(expected)
    assert lab.paired_bootstrap(errors, usual) == (difference, interval)
    assert lab.paired_bootstrap([1] * 20, [2] * 20) == (-1, (-1, -1))
    assert lab.paired_bootstrap([3] * 20, [2] * 20) == (1, (1, 1))


def test_small_medium_reports_its_actual_count_without_fitting(conn, monkeypatch):
    rows = [(key, 7.0, 7.0) for key in range(19)]
    context = lab.Context(conn, "anime", dict.fromkeys(range(19), 7.0), date(1970, 1, 1))
    monkeypatch.setattr(lab, "load_data", lambda *_: (rows, {}, context))

    def refuse(*_):
        pytest.fail("A small sample must not be fitted")

    result = lab.evaluate(conn, "anime", [lab.Candidate("quiet", refuse)])[0]
    assert result == {
        "candidate": "quiet",
        "count": 19,
        "mae": None,
        "whole_mae": None,
        "spearman": None,
        "top_hits": None,
        "vs_usual": None,
        "interval": None,
        "verdict": None,
        "seconds": None,
    }


def test_experiments_are_isolated_from_profile_switches_and_match_existing_apply(library):
    library.execute(
        "INSERT INTO rec_dismissed (kind, item_key, dismissed_at) VALUES ('anime', '1', ?)",
        ("2026-10-14 00:00:00",),
    )
    rows, facts, context = lab.load_data(library, "anime")
    before = [lab.holdout_predictions(rows, facts, context, c) for c in lab.DEFAULT_CANDIDATES]
    assert before[0] != before[1]
    assert before[0] != before[2]
    for key in ("rec_dismissal_signal", "rec_recent_ratings", "rec_theme_transfer"):
        settings.set_value(library, key, True)
    for candidate, expected in zip(lab.DEFAULT_CANDIDATES, before, strict=True):
        actual = lab.holdout_predictions(rows, facts, context, candidate)
        assert actual == expected
        switches = {
            "dismissal": candidate.name == "dismissals",
            "recency": candidate.name == "recency",
        }
        assert [mine - guess for _, mine, guess in actual] == experiments.residuals(
            library, "anime", rows, facts, context.now, transfer=False, **switches
        )


def test_recency_reference_is_stored_data_and_books_without_dates_use_epoch(library):
    assert lab.load_data(library, "anime")[2].now == date(2026, 10, 13)
    assert lab.load_data(library, "film")[2].now == date(2026, 10, 10)
    assert lab.load_data(library, "tv")[2].now == date(2026, 10, 14)
    assert lab.load_data(library, "book")[2].now == date(1970, 1, 1)


def test_adult_anime_counts_with_visibility_off(library):
    library.execute("UPDATE stg_mal_anime SET nsfw_rating = 'black' WHERE mal_anime_id = 1")
    settings.set_value(library, "include_nsfw", False)
    hidden = lab.evaluate(library, "anime")
    settings.set_value(library, "include_nsfw", True)
    visible = lab.evaluate(library, "anime")
    assert all(r["count"] == 343 for r in hidden)
    assert [{k: v for k, v in r.items() if k != "seconds"} for r in hidden] == [
        {k: v for k, v in r.items() if k != "seconds"} for r in visible
    ]


def test_evaluation_verdicts_compare_candidates_on_the_same_titles(library):
    rows, facts, context = lab.load_data(library, "anime")
    usual = {
        key: guess
        for key, _, guess in lab.holdout_predictions(
            rows, facts, context, lab.DEFAULT_CANDIDATES[0]
        )
    }
    # Deliberate oracles test the evaluator only, not a candidate's learning procedure.
    actual = {key: mine for key, mine, _ in rows}
    candidates = [
        lab.Candidate("perfect", lambda *_: actual.__getitem__),
        lab.Candidate("terrible", lambda *_: lambda key: 1.0 if actual[key] >= 5.5 else 10.0),
        lab.Candidate("copy", lambda *_: usual.__getitem__),
    ]
    results = lab.evaluate(library, "anime", candidates)
    assert [r["verdict"] for r in results] == ["better", "worse", "same"]
    assert results[0]["spearman"] == pytest.approx(1)
    assert results[0]["top_hits"] == 1
    assert results[0]["mae"] == 0
    assert results[2]["vs_usual"] == 0
    assert results[2]["interval"] == (0, 0)


def test_prediction_at_exactly_the_minimum_and_an_empty_training_fold(conn):
    rows = [(5 * i, 8.0, 7.0) for i in range(20)]
    facts = {key: {"genres": [], "studios": []} for key, _, _ in rows}
    context = lab.Context(conn, "anime", {key: crowd for key, _, crowd in rows}, date(1970, 1, 1))
    predictions = lab.holdout_predictions(rows, facts, context, lab.DEFAULT_CANDIDATES[0])
    assert len(predictions) == 20
    assert all(guess == build_model([], facts).predict(7, [], []) for _, _, guess in predictions)


def test_lab_leaves_every_table_and_saved_recommendation_unchanged(library):
    recommend.compute_all(library, NOW_DT, ("anime", "film", "tv", "book", "everything"))
    tables = [
        row[0] for row in library.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    ]
    before_counts = {
        table: library.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables
    }
    before = list(library.iterdump())
    for medium in lab.MEDIA:
        lab.evaluate(library, medium)
    assert list(library.iterdump()) == before
    assert {
        table: library.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables
    } == before_counts
