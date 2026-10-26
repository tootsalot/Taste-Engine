"""Each rated list can keep Usual or choose an experimental rating model."""

import json

import pytest

from taste import settings
from taste.recommend.runs import REC_SETTINGS, save_run

MODELS = {
    "usual": "Usual",
    "details": "More details",
    "similar": "Similar titles",
    "blend": "Blend",
}


@pytest.mark.parametrize("medium", ("anime", "film", "tv", "book"))
def test_model_settings_are_advanced_choices_that_default_to_usual(conn, medium):
    key = f"rec_model_{medium}"
    setting = settings.BY_KEY[key]
    assert setting.kind == "choice"
    assert setting.default == "usual"
    assert setting.choices == MODELS
    assert setting.group == "Recommendations"
    assert setting.advanced is True
    assert setting.help.endswith(".")
    for word in ("ratings", "cached", "experimental", "lab"):
        assert word in setting.help.lower()
    assert settings.get(conn, key) == "usual"
    assert key in REC_SETTINGS
    for name in MODELS:
        assert settings.set_value(conn, key, name) == name
        assert settings.get(conn, key) == name
    with pytest.raises(settings.SettingError, match="pick one"):
        settings.set_value(conn, key, "example_unknown_model")


def test_model_choices_are_saved_with_each_recommendation_run(conn):
    expected = {
        "rec_model_anime": "usual",
        "rec_model_film": "details",
        "rec_model_tv": "similar",
        "rec_model_book": "blend",
    }
    for key, value in expected.items():
        settings.set_value(conn, key, value)
    run_id = save_run(conn, "film", [], {})
    params = json.loads(
        conn.execute("SELECT params_json FROM rec_runs WHERE rec_run_id = ?", (run_id,)).fetchone()[
            0
        ]
    )
    assert {key: params[key] for key in expected} == expected
    assert params["film_prior_votes"] == 1000


@pytest.mark.parametrize(
    "key", ("rec_dismissal_signal", "rec_recent_ratings", "rec_theme_transfer")
)
def test_existing_experiment_help_limits_the_signal_to_usual(key):
    setting = settings.BY_KEY[key]
    assert setting.default is False
    assert setting.advanced is True
    assert "only lists set to usual" in setting.help.lower()
