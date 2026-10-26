"""Model choice uses per-profile state and comparable held-out rating checks."""

from datetime import date, datetime

import pytest

from taste import db, recommend, settings
from taste.recommend import candidates, comparison, lab, selection


def test_answered_model_version_is_per_profile_and_reads_the_current_version(
    conn, tmp_path, monkeypatch
):
    assert recommend.MODEL_SET_VERSION == 1
    assert comparison.asked_version(conn) == 0
    comparison.mark_asked(conn)
    assert comparison.asked_version(conn) == 1
    row = conn.execute(
        "SELECT state_value, updated_at FROM app_state WHERE state_key = 'rec_model_asked'"
    ).fetchone()
    assert row[0] == "1"
    datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S")
    other = db.connect(tmp_path / "other.db")
    try:
        assert comparison.asked_version(other) == 0
        monkeypatch.setattr(recommend, "MODEL_SET_VERSION", 2)
        assert comparison.asked_version(conn) == 1
        comparison.mark_asked(conn)
        assert comparison.asked_version(conn) == 2
        assert comparison.asked_version(other) == 0
    finally:
        other.close()


@pytest.mark.parametrize("value", ("", "unknown", "-1", "1.5", '"1"'))
def test_invalid_answered_version_is_treated_as_unanswered(conn, value):
    conn.execute(
        "INSERT INTO app_state (state_key, state_value, updated_at) VALUES (?, ?, ?)",
        ("rec_model_asked", value, "2026-10-14 18:00:00"),
    )
    assert comparison.asked_version(conn) == 0


@pytest.mark.parametrize("name", settings.REC_MODELS)
def test_choosing_a_model_sets_all_four_lists_and_marks_this_version(conn, monkeypatch, name):
    monkeypatch.setattr(recommend, "MODEL_SET_VERSION", 3)
    before_count = settings.get(conn, "rec_count")
    comparison.apply_choice(conn, name)
    assert {settings.get(conn, f"rec_model_{medium}") for medium in lab.MEDIA} == {name}
    assert comparison.asked_version(conn) == 3
    assert settings.get(conn, "rec_count") == before_count
    assert conn.execute("SELECT COUNT(*) FROM rec_runs").fetchone()[0] == 0


def test_an_invalid_model_choice_changes_neither_settings_nor_answered_version(conn):
    for medium, name in zip(lab.MEDIA, settings.REC_MODELS, strict=True):
        settings.set_value(conn, f"rec_model_{medium}", name)
    before = settings.get_all(conn)
    with pytest.raises(settings.SettingError):
        comparison.apply_choice(conn, "made_up_model")
    assert settings.get_all(conn) == before
    assert comparison.asked_version(conn) == 0


@pytest.mark.parametrize(
    ("kind", "distance"),
    (
        ("anime", "within 1 point of your score"),
        ("film", "within 1 point of your score"),
        ("tv", "within 1 point of your score"),
        ("book", "within half a star of your score"),
        ("all", "within 1 point of your score (half a star for books)"),
    ),
)
def test_accuracy_uses_plain_words_and_native_book_stars(kind, distance):
    block = {"count": 100, "within_one": 0.61, "top_hits": 0.5, "verdict": "same"}
    assert comparison.accuracy_text(kind, block) == (
        f"Guessed {distance} 61% of the time. "
        "Of the titles you rated highest, it ranked 5 in 10 near the top."
    )


@pytest.mark.parametrize("count", (0, 1, 19))
def test_sparse_accuracy_explains_that_more_ratings_are_needed(count):
    block = {"count": count, "within_one": None, "top_hits": None, "verdict": None}
    assert comparison.accuracy_text("anime", block) == "Not enough ratings to check yet"


def test_a_profile_without_twenty_ratings_in_any_medium_never_fits_models(conn, monkeypatch):
    counts = {"anime": 19, "film": 1, "tv": 0, "book": 19}

    def load(_conn, medium):
        rows = [(key, 7.0, 7.0) for key in range(1, counts[medium] + 1)]
        return rows, {}, None

    def refuse(*args, **kwargs):
        pytest.fail("Small media have no held-out accuracy and need no fitted models")

    monkeypatch.setattr(lab, "load_data", load)
    monkeypatch.setattr(selection, "calibration", refuse, raising=False)
    result = comparison.check_models(conn)
    assert result["count"] == 0
    assert set(result["models"]) == set(settings.REC_MODELS)
    for block in result["models"].values():
        assert block["count"] == 0
        assert block["within_one"] is None
        assert block["top_hits"] is None
        assert block["verdict"] is None
        assert set(block["media"]) == set(lab.MEDIA)
        for medium, row in block["media"].items():
            assert row["count"] == counts[medium]
            assert row["within_one"] is None
            assert row["top_hits"] is None
            assert row["verdict"] is None
    assert conn.execute("SELECT COUNT(*) FROM rec_model_calibrations").fetchone()[0] == 0


@pytest.fixture
def invented_checks(conn, monkeypatch):
    counts = {"anime": 20, "film": 40, "tv": 19, "book": 0}
    rows = {
        medium: [(key, 5.0 if key <= count * 0.8 else 9.0, 7.0) for key in range(1, count + 1)]
        for medium, count in counts.items()
    }
    fitted = []

    def load(_conn, medium):
        facts = {key: {} for key, _, _ in rows[medium]}
        context = candidates.Context(conn, medium, dict.fromkeys(facts, 7.0), date(2026, 10, 14))
        return rows[medium], facts, context

    def model(name):
        def fit(train, facts, context, *extra):
            train_ids = {key for key, _, _ in train}
            assert len(train) < counts[context.medium]
            fitted.append((name, context.medium, train_ids))

            def predict(key):
                assert key not in train_ids
                actual = 5.0 if key <= counts[context.medium] * 0.8 else 9.0
                if name == "details":
                    return actual if context.medium == "anime" else 1.0
                return actual - 1 if name == "similar" else 5.0

            return predict

        return fit

    monkeypatch.setattr(lab, "load_data", load)
    for name in settings.REC_MODELS:
        monkeypatch.setattr(candidates, f"fit_{name}", model(name))
    return rows, fitted


def test_checks_weight_qualified_media_by_count_and_keep_small_media_visible(conn, invented_checks):
    result = comparison.check_models(conn)
    assert result["count"] == 60
    assert result["models"]["usual"]["within_one"] == pytest.approx(0.8)
    assert result["models"]["usual"]["top_hits"] == 0
    details = result["models"]["details"]
    assert details["count"] == 60
    assert details["within_one"] == pytest.approx(1 / 3)
    assert details["top_hits"] == pytest.approx(1 / 3)
    assert details["media"]["anime"]["within_one"] == 1
    assert details["media"]["anime"]["top_hits"] == 1
    assert details["media"]["film"]["within_one"] == 0
    assert details["media"]["film"]["top_hits"] == 0
    assert details["media"]["tv"]["count"] == 19
    assert details["media"]["tv"]["within_one"] is None
    assert details["media"]["book"]["count"] == 0
    assert details["media"]["book"]["top_hits"] is None
    assert result["models"]["similar"]["within_one"] == 1
    assert result["models"]["similar"]["top_hits"] == 1
    assert details["media"]["anime"]["verdict"] == "better"
    assert details["media"]["film"]["verdict"] == "worse"
    assert details["verdict"] == "worse"
    assert result["models"]["blend"]["verdict"] == "same"


def test_rechecking_reuses_cached_folds_and_only_a_changed_medium_refits(conn, invented_checks):
    rows, fitted = invented_checks
    first = comparison.check_models(conn)
    assert fitted
    fitted.clear()
    assert comparison.check_models(conn) == first
    assert fitted == []
    rows["anime"][0] = (1, 6.0, 7.0)
    comparison.check_models(conn)
    assert fitted
    assert {medium for _, medium, _ in fitted} == {"anime"}
    assert conn.execute("SELECT COUNT(*) FROM rec_runs").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("interval", "verdict"),
    (((-0.4, -0.01), "better"), ((-0.4, 0), "same"), ((0, 0.4), "same"), ((0.01, 0.4), "worse")),
)
def test_a_better_verdict_requires_the_whole_paired_interval_below_zero(
    conn, invented_checks, monkeypatch, interval, verdict
):
    monkeypatch.setattr(lab, "paired_bootstrap", lambda *_: (-0.1, interval))
    result = comparison.check_models(conn)
    assert result["models"]["details"]["verdict"] == verdict
    assert result["models"]["details"]["media"]["anime"]["verdict"] == verdict


def test_checks_announce_work_only_after_eligibility_is_known(conn, invented_checks):
    progress = []
    assert comparison.check_models(conn, progress=progress.append)["count"] == 60
    assert progress == ["Checking each model against your ratings"]


def test_a_cancelled_check_stops_without_fitting_or_marking_a_profile(conn, invented_checks):
    _, fitted = invented_checks
    assert comparison.check_models(conn, cancelled=lambda: True) is None
    assert fitted == []
    assert comparison.asked_version(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM rec_model_calibrations").fetchone()[0] == 0


def test_cancellation_between_models_prevents_further_fits(conn, invented_checks):
    _, fitted = invented_checks
    assert comparison.check_models(conn, cancelled=lambda: bool(fitted)) is None
    assert {name for name, _, _ in fitted} == {"usual"}
    assert {medium for _, medium, _ in fitted} == {"anime"}
    assert comparison.asked_version(conn) == 0


def test_an_invalid_version_changes_nothing_inside_an_existing_transaction(conn):
    before = settings.get_all(conn)
    conn.execute("BEGIN")
    try:
        with pytest.raises(ValueError):
            comparison.apply_choice(conn, "blend", version="invalid")
        assert settings.get_all(conn) == before
        assert comparison.asked_version(conn) == 0
    finally:
        conn.execute("ROLLBACK")
